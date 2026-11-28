"""
大模型核心能力封装模块（LLM Service Layer）
【业务含义】：封装与大模型交互的四大核心场景：
  1. 普通一问一答对话（`chat`）
  2. 流式打字机对话（`chat_stream`）
  3. 结构化 JSON 信息抽取（`chat_json` + `ExtractedProfile`）
  4. 工具调用 / Agent 雏形（`chat_with_tools`：查天气、精确四则运算）
  并统一记录每次大模型调用的耗时、Token 消耗与费用日志。
【技术含义】：相当于后端分层架构中的 Service / Infrastructure 层，把第三方 SDK（`AsyncOpenAI`）细节与 Web 路由层（`main.py`）解耦，方便复用和单元测试打桩（Mock）。
"""

import json       # 标准库：JSON 序列化与反序列化
import logging    # 标准库：日志记录
import time       # 标准库：高精度计时
import ast        # 标准库：抽象语法树（Abstract Syntax Tree），用于安全解析数学算式，替代危险的 eval()
import inspect    # 标准库：运行时自省工具（当前文件暂未使用）
import operator   # 标准库：将 + - * / 等运算符映射为可调用的函数对象
import re         # 标准库：正则表达式，用于从混杂文本中兜底提取 JSON
import httpx      # 第三方异步 HTTP 客户端（类似 Java WebClient / OkHttp 异步模式），用于非阻塞请求外部天气接口

# 使用 OpenAI 官方异步客户端 `AsyncOpenAI` 而非同步 `OpenAI`：
# 为什么这么写：FastAPI 运行在异步事件循环（Event Loop）上，LLM 接口动辄耗时数秒，必须用异步 I/O 才能在等待模型吐字时让出线程去处理其他并发请求。
from openai import AsyncOpenAI

from ai_service.config import settings

from pydantic import BaseModel, Field, ValidationError

# 创建命名 Logger（名称带层级 `ai-service.llm`），会自动继承根 logger `ai-service` 的级别和格式，同时在日志里能区分出是哪个模块打的日志
logger = logging.getLogger("ai-service.llm")

# 模块私有全局变量（前缀 `_` 按 Python 约定表示“内部私有，外部不要直接访问”）。
# 类型注解 `AsyncOpenAI | None`（Python 3.10+ 联合类型语法，等价于 `Optional[AsyncOpenAI]`）表示初始为空，初始化后为客户端实例。
_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    """
    【业务含义】：获取大模型异步客户端（懒加载单例）。
    【为什么这么写（懒加载 Lazy Initialization）】：
    1. 不在模块导入时立刻创建 `AsyncOpenAI`，而是等到真正发请求时才创建：
       这样即使本地还没配 `.env` 里的 `LLM_API_KEY`，服务也能正常启动、跑通 `/health` 和不调真实大模型的单元测试。
    2. 复用同一个 `_client` 实例：底层会复用 HTTP 连接池（Keep-Alive），避免每次对话都重新做 TCP + TLS 握手。
    """
    # `global _client` 声明下面要修改的是模块顶层的全局变量 `_client`，否则 Python 会把赋值语句里的 `_client` 当成函数局部变量而报错
    global _client
    if _client is None:
        if not settings.llm_api_key:
            # 快速失败（Fail-Fast）：给出明确中文排障提示，而不是让 SDK 报晦涩的鉴权异常
            raise RuntimeError("LLM_API_KEY 未配置：先在 .env 里填上百炼 Key")
        _client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    return _client


def log_llm_call(model: str, input_tokens: int, output_tokens: int, duration_ms: float) -> None:
    """
    【业务含义】：统一记录大模型调用的可观测性指标（模型名、输入/输出 Token 数、耗时毫秒、预估花费）。
    【为什么这么写】：生产环境中大模型按 Token 计费且延迟高，集中打结构化 `key=value` 日志便于后续接入日志系统做账单对账与慢查询监控。
    """
    cost = None
    # 只有配置了输入单价（> 0）时才计算费用；`1_000_000` 中的下划线是 Python 数字分隔符语法，纯粹为了人眼易读，等价于 `1000000`
    if settings.llm_price_input_per_1m > 0:
        cost = round(
            input_tokens / 1_000_000 * settings.llm_price_input_per_1m
            + output_tokens / 1_000_000 * settings.llm_price_output_per_1m,
            6,  # 保留 6 位小数，因为单次短对话可能只花几厘钱
        )
    # 为什么用 `%s` 占位符而不是 f-string：logging 模块支持惰性格式化，若当前日志级别高于 INFO（如 WARNING），则不会执行字符串拼接开销
    logger.info(
        "llm_call model=%s input_tokens=%s output_tokens=%s duration_ms=%s cost=%s",
        model, input_tokens, output_tokens, round(duration_ms), cost,
    )


async def chat(messages: list[dict], temperature: float = 0.7, max_tokens: int = 1024) -> dict:
    """
    【业务含义】：非流式普通对话——把完整上下文发给模型，等模型全部生成完毕后一次性返回结果与统计指标。
    【参数说明】：
    - `messages`：符合 OpenAI 协议的消息列表，如 `[{"role": "system", ...}, {"role": "user", ...}]`
    - `temperature`：采样温度（0~2），越低回答越确定严谨，越高越发散有创意；默认 0.7 适合日常对话
    - `max_tokens`：限制模型单次最多生成的 Token 数，防止失控吐字烧钱或超时
    """
    client = get_client()
    # 为什么用 `time.monotonic()` 而不是 `time.time()`：
    # `monotonic()` 是单调递增时钟，不受系统时间被 NTP 校时回拨影响，专门用来精确测量时间间隔
    start = time.monotonic()
    # `await` 挂起当前协程等待网络返回，期间事件循环可以去处理其他用户的请求，不会阻塞线程
    resp = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    duration_ms = (time.monotonic() - start) * 1000
    usage = resp.usage  # 模型返回的 Token 消耗统计对象
    log_llm_call(settings.llm_model, usage.prompt_tokens, usage.completion_tokens, duration_ms)
    # `resp.choices[0].message.content`：模型一次请求可生成多条候选回复（参数 `n` 默认 1），所以取第 0 条候选的文本内容
    return {
        "reply": resp.choices[0].message.content,
        "model": resp.model,
        "input_tokens": usage.prompt_tokens,
        "output_tokens": usage.completion_tokens,
        "duration_ms": round(duration_ms),
    }


async def chat_stream(messages: list[dict], temperature: float = 0.7, max_tokens: int = 1024):
    """
    【业务含义】：流式对话——像 ChatGPT 网页一样，模型每算出几个字就立刻往前端推几个字，大幅降低用户感知的“首字等待时间（TTFT）”。
    【技术含义】：`async def` 内部包含 `yield`，使该函数成为“异步生成器（Async Generator）”，外部可用 `async for` 逐片消费数据。
    """
    client = get_client()
    start = time.monotonic()
    stream = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,  # 开启服务端流式推送
        # 默认流式模式下中间分片不含 usage，必须传 `include_usage: True`，服务端才会在最后一个结束分片里附带总 Token 统计
        stream_options={"include_usage": True},
    )
    # `async for` 异步迭代服务端推过来的每一个增量分片（chunk）
    async for chunk in stream:
        # 注意：非流式取的是 `message.content`（完整消息），流式取的是 `delta.content`（本次新增的几个字符）；
        # 且最后一条纯 usage 分片的 `choices` 可能为空列表，所以要先判空 `if chunk.choices and chunk.choices[0].delta.content`
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
        # 当收到携带 usage 的尾部分片时，记录整次流式对话的总 Token 和总耗时
        if chunk.usage:
            log_llm_call(
                settings.llm_model,
                chunk.usage.prompt_tokens,
                chunk.usage.completion_tokens,
                (time.monotonic() - start) * 1000,
            )


class ExtractedProfile(BaseModel):
    """
    【业务含义】：从一段非结构化自我介绍文本中抽取出的“用户画像”标准数据结构。
    【技术含义】：继承 Pydantic `BaseModel`，既用于校验大模型吐出的 JSON 字段类型是否合法，也直接作为 FastAPI 接口的响应 Schema。
    ⚠️【潜在字段名不一致提示】：下面字段名叫 `year_experience`（单数），而 `main.py` `/exact` 接口的提示词里写的是 `years_experience`（复数），
       会导致模型返回的 `years_experience` 无法赋给 `year_experience`（该字段保持默认值 `None`）。建议两边统一字段名。
    """
    name: str = ""
    age: int | None = None              # 允许为整数或空（文本未提及时模型填 null）
    role: str = ""
    year_experience: int | None = None
    target_direction: str = ""


def parse_json_loose(text: str) -> dict:
    r"""
    【业务含义】：宽松解析模型返回的 JSON 字符串。
    【为什么这么写】：即使在提示词里要求“只返回 JSON”，部分大模型仍可能习惯性包裹一层 Markdown 代码块（如 ```json {...} ```）或前后客套话。
    先尝试标准 `json.loads`；如果失败，再用正则 `r"\{.*\}"`（配合 `re.S` 让 `.` 能跨行匹配换行符）把最外层 `{` 到 `}` 抠出来再解析，提高鲁棒性。
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        # `raise` 不带参数表示把刚才捕获到的原始 `JSONDecodeError` 原样往上抛，保留完整报错堆栈
        raise


async def chat_json(message: list[dict], temperature: float = 0.3) -> dict:
    """
    【业务含义】：结构化抽取专用对话——要求模型输出合法 JSON 对象并解析为 Python 字典返回。
    【为什么温度默认 0.3】：信息抽取任务需要模型忠实原文、格式稳定，不需要发挥想象力，因此调低 `temperature`。
    """
    client = get_client()
    start = time.monotonic()
    resp = await client.chat.completions.create(
        model=settings.llm_model,
        messages=message,
        temperature=temperature,
        # 【技术本意】：开启 JSON Mode，强制模型在解码阶段只输出语法合法的 JSON 对象。
        # ⚠️【语法 Bug 提示】：这里写成了逗号 `{"type","json_object"}`，在 Python 中这是集合（`set`）字面量而不是字典（`dict`），
        #    实际调用时会触发序列化/参数类型报错！正确写法应为冒号：`response_format={"type": "json_object"}`。
        response_format= {"type","json_object"},

    )
    duration_ms = (time.monotonic()-start)*1000
    usage = resp.usage
    log_llm_call(settings.llm_model,usage.prompt_tokens,usage.completion_tokens,duration_ms)
    return parse_json_loose(resp.choices[0].message.content)


'''
以下专治模型算数不准 —— Function Calling（工具调用）机制
【业务背景】：大语言模型本质是“下一个词概率预测器”，既无法联网获取实时天气，也不擅长多位数的精确四则运算。
【解决思路】：给模型提供“工具说明书（TOOLS）”，由模型判断何时需要调工具并生成调用参数 -> 我们的 Python 代码真正执行工具 -> 把结果喂回给模型生成最终人话回复。
'''


# `TOOLS` 是遵照 OpenAI Function Calling JSON Schema 规范编写的“工具说明书”列表，会随请求一起发给大模型。
# 注意：`description` 非常关键！它不是写给程序员看的注释，而是直接作为 Prompt 一部分喂给大模型，模型完全靠读 `description` 决定调不调该工具、怎么传参。
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的实时天气，返回温度、天气状况和风速。",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "城市名，如 北京、上海"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "计算一个四则运算算式，如 128*46。模型自己算数不可靠时调用它，不要自己心算。",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "只含数字、括号和 + - * / 的算式"}},
                "required": ["expression"],
            },
        },
    },
]

# 白名单映射表：把 AST 语法树中的加减乘除节点类型，映射到标准库 `operator` 对应的数学运算函数
_CALC_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def _eval(node):
    """
    【业务含义】：递归计算数学算式语法树的值。
    【为什么这么写（安全考量）】：
    Python 内置的 `eval(expression)` 虽然一行就能算算式，但如果模型或恶意用户输入 `__import__('os').system('rm -rf /')` 就会导致远程代码执行漏洞（RCE）！
    用 `ast.parse` 把字符串解析成语法树后，只允许“数字常量、加减乘除二元运算、负号一元运算”这几种白名单节点通过，彻底杜绝任意代码执行风险。
    """
    # 顶层表达式包装节点，继续剥开算里面的主体 `node.body`
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    # 叶子节点：纯数字常量（int 或 float），排除字符串/布尔等其他常量
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    # 二元运算节点（如 `a + b`、`a * b`）：递归算出左子树和右子树的值，再用映射表里的运算符函数计算
    if isinstance(node, ast.BinOp) and type(node.op) in _CALC_OPS:
        return _CALC_OPS[type(node.op)](_eval(node.left), _eval(node.right))
    # 一元负号节点（如 `-5` 或 `-(3 + 2)`）
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand)
    # 任何不在白名单里的语法（如函数调用、属性访问、幂运算等）一律拒绝
    raise ValueError("不支持的算式")


async def get_weather(city: str) -> str:
    """
    【业务含义】：真实外部工具 1 —— 调用公开天气服务 `wttr.in` 查询指定城市的实时天气文本摘要。
    【技术含义】：
    - `async with httpx.AsyncClient(timeout=10) as c`：异步上下文管理器，代码块结束时自动关闭连接释放资源；设置 10 秒超时防止外部接口挂死拖垮本服务。
    - `r.raise_for_status()`：若 HTTP 状态码是 4xx/5xx 则主动抛出异常，交给外层 `run_tool` 统一兜底。
    """
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"https://wttr.in/{city}", params={"format": "j1"})
        r.raise_for_status()
        cur = r.json()["current_condition"][0]
        return f"{city}：{cur['weatherDesc'][0]['value']}，{cur['temp_C']}°C，体感 {cur['FeelsLikeC']}°C，风速 {cur['windspeedKmph']} km/h"


async def calculate(expression: str) -> str:
    """
    【业务含义】：真实本地工具 2 —— 安全计算四则运算表达式并返回结果字符串。
    【技术含义】：`ast.parse(expression, mode='eval')` 将字符串按单个表达式模式解析为 AST 语法树，再交由 `_eval` 安全求值。
    """
    try:
        return f"{expression} = {_eval(ast.parse(expression, mode='eval'))}"
    except Exception as e:
        return f"算式错误：{e}"


# 工具注册表（函数字典）：Python 中函数是一等公民，可以直接作为字典的值存起来，方便按工具名字符串动态路由调用（替代一堆 if-elif）
TOOL_FUNCS = {"get_weather": get_weather, "calculate": calculate}


async def run_tool(name: str, args: dict) -> str:
    """
    【业务含义】：工具统一调度执行器。
    【为什么报错不抛异常而是返回字符串】：
    在 Agent 循环中，大模型可能会传错参数名或算式格式。如果直接 raise 异常会让整个 HTTP 接口 500 崩溃；
    把错误包装成一段自然语言字符串返回给模型，模型看到报错后往往能在下一轮自动修正参数重试，或者礼貌告知用户原因。
    """
    fn = TOOL_FUNCS.get(name)
    if fn is None:
        return f"没有这个工具：{name}"
    try:
        # `**args` 是字典解包语法：例如 `args = {"city": "杭州"}` 会变成关键字参数调用 `await get_weather(city="杭州")`
        return await fn(**args)
    except Exception as e:
        return f"工具执行失败：{e}，请检查参数后重试或换一种方式回答"


async def chat_with_tools(messages: list[dict], max_rounds: int = 3) -> dict:
    """
    【业务含义】：完整的 Agent 工具调用闭环（ReAct / Tool-Use Loop）：
      第 1 步：把用户问题 + 工具说明书发给模型；
      第 2 步：若模型觉得不需要工具，直接返回最终回答；若需要工具，模型会返回 `tool_calls` 指令列表（支持一次并行调多个工具）；
      第 3 步：本地代码逐个执行工具，把工具结果拼回对话历史 `msgs`，再次调用模型，直到模型给出最终自然语言回复或达到最大轮数。
    【为什么限制 `max_rounds=3`】：防止模型陷入死循环（反复调错同一个工具）导致无限消耗 Token 和接口挂起。
    """
    client = get_client()
    # `list(messages)` 浅拷贝一份消息列表：避免直接往调用方传入的原列表里 `append` 造成副作用污染外部变量
    msgs = list(messages)
    used_tools: list[dict] = []  # 记录本次请求实际调用过的工具轨迹，方便前端展示或调试验证
    for _ in range(max_rounds):
        start = time.monotonic()
        resp = await client.chat.completions.create(
            model=settings.llm_model, messages=msgs, tools=TOOLS, temperature=0.7,
        )
        usage = resp.usage
        log_llm_call(settings.llm_model, usage.prompt_tokens, usage.completion_tokens,
                     (time.monotonic() - start) * 1000)
        msg = resp.choices[0].message
        # 终止条件：模型没有发出 `tool_calls`，说明它已经拿到了足够信息并生成了最终回答 `msg.content`
        if not msg.tool_calls:
            return {"reply": msg.content, "used_tools": used_tools}

        # 【关键协议细节】：必须先把模型本轮返回的 assistant 消息（内含 `tool_calls` 列表及每个调用的唯一 `id`）原样放进上下文，
        # 紧接着才能放 `role="tool"` 的结果消息，且 `tool_call_id` 必须一一对应，否则大模型网关会报上下文协议校验错误！
        msgs.append(msg)  # 模型"要调工具"的决定原样追加回去，不能只追加工具结果
        for call in msg.tool_calls:
            name = call.function.name
            # 模型生成的 `arguments` 是 JSON 格式字符串，需反序列化为字典；偶发格式损坏时兜底为空字典 `{}`，交给 `run_tool` 报错引导修正
            try:
                args = json.loads(call.function.arguments)
            except json.JSONDecodeError:
                args = {}
            result = await run_tool(name, args)
            used_tools.append({"tool": name, "args": args})
            # 按 OpenAI 协议回填工具执行结果：`role` 固定为 `"tool"`，`tool_call_id` 绑定对应调用 ID
            msgs.append({"role": "tool", "tool_call_id": call.id, "content": result})

    # 兜底保护：跑满 `max_rounds` 轮模型还在要求调工具时强制刹车返回
    return {"reply": "工具调用超过最大轮数，已停止", "used_tools": used_tools}
