"""
Web 接口入口与路由控制层（Controller / Router Layer）
【业务含义】：对外暴露整个 HTTP 服务的全部 RESTful API，包含三大块业务：
  1. Web 基础能力演示：健康检查、问候接口、用户的增查与分页、全局统一异常格式；
  2. Python 并发原理对比实验：同步线程池（`def`） vs 异步非阻塞（`async def` + `await`） vs 异步误用阻塞事件循环；
  3. 大模型业务接口：普通对话（`/chat`）、SSE 流式对话（`/chat/stream`）、结构化信息抽取（`/exact`）、带工具调用对话（`/chat/tools`）。
【技术含义】：基于 FastAPI + Pydantic 构建（定位类似 Java 生态里的 Spring Boot `@RestController` + `@Valid` DTO 校验）。
"""

# main.py
import time
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from enum import Enum
import logging
from ai_service.config import settings
import asyncio
import json
from fastapi.responses import StreamingResponse
# ⚠️【提示】：下面第 23 行重复导入了 `BaseModel, Field`（第 15 行已导入），不影响运行但可清理。
from pydantic import BaseModel, Field
from ai_service import llm
from ai_service.llm import ExtractedProfile

# 创建 FastAPI 核心应用实例（类似 SpringApplication 上下文）。
# `title="ai-service"` 会显示在 FastAPI 自动生成的交互式接口文档页面（启动后访问 `/docs` 即 Swagger UI，`/redoc` 即 ReDoc）。
app = FastAPI(title="ai-service")


# ==================== 第一部分：基础数据模型（DTO / VO） ====================
# 技术说明：继承 `BaseModel` 的类相当于 Java 中的请求 DTO / 响应 VO。
# FastAPI 会自动完成：请求 JSON 反序列化 -> 字段类型转换与约束校验（不合法自动返回 HTTP 422 明细）-> 响应对象过滤与序列化。

class HelloReq(BaseModel):
    """问候接口的请求体模型"""
    # `Field(...)` 用于声明校验规则和接口文档元数据（类似 Java Bean Validation 的 `@Size(min=1)` + Swagger `@Schema`）
    name: str = Field(min_length=1, description="姓名")


class UserOut(BaseModel):
    """用户信息脱敏输出模型（VO）：控制接口只向外暴露这 3 个字段，哪怕底层字典里有密码等敏感字段也会被自动过滤掉"""
    user_id: int
    name: str
    email: str


class Role(str, Enum):
    """
    用户角色枚举类
    【为什么同时继承 `str` 和 `Enum`】：
    只继承 `Enum` 时 JSON 序列化可能需要取 `.value`；同时继承 `str` 后，枚举成员本身就是字符串子类，
    既能限定入参只能传 `"admin"` / `"user"` / `"guest"` 三者之一（传别的自动报 422），又能直接无缝序列化为 JSON 字符串。
    """
    ADMIN = "admin"
    USER = "user"
    GUEST = "guest"


class Address(BaseModel):
    """嵌套子对象模型：演示 Pydantic 支持多层嵌套 JSON 自动校验"""
    city: str = Field(min_length=1)
    street: str = Field(min_length=1)


class UserCreate(BaseModel):
    """创建用户的请求体 DTO"""
    name: str = Field(min_length=2, max_length=20, description="姓名请输入2-20个字符")
    # `ge=0, le=500` 即 greater than or equal (>=0) 和 less than or equal (<=500)
    age: int = Field(ge=0, le=500, description="年龄，0-500")
    email: str
    # 带默认值的枚举字段：请求 JSON 不传 `role` 时默认赋为普通用户 `Role.USER`
    role: Role = Role.USER
    # `Address | None = None`：可选嵌套对象，前端可以传 `{city, street}`，也可以传 `null` 或干脆不传该字段
    address: Address | None = None


# 内存字典模拟数据库表（Key 为主键 `user_id`，Value 为行记录字典）；注意重启进程后新增数据会重置
USERS = {
    1: {"user_id": 1, "name": "zhangsan", "email": "zhangsan@email.com"},
    2: {"user_id": 2, "name": "lisi", "email": "lisi@email.com"},
    3: {"user_id": 3, "name": "wangwu", "email": "wangwu@email.com"},
}


# ==================== 第二部分：基础 CRUD 与健康检查路由 ====================

# `@app.post(...)` 是路由装饰器（类似 Spring `@PostMapping("/create")`）：
# - `response_model=UserOut`：声明返回结构，FastAPI 会用 `UserOut` 校验并裁剪返回值，同时生成准确的 OpenAPI 文档；
# - `status_code=201`：遵循 RESTful 规范，新建资源成功返回 201 Created 而非默认 200。
@app.post("/create", response_model=UserOut, status_code=201)
def create_user(req: UserCreate):
    """【业务】：新增一名用户并返回创建后的用户公开信息。
    【技术】：参数 `req: UserCreate` 类型为 BaseModel 子类，FastAPI 自动识别它来自 HTTP 请求体（Request Body JSON）。"""
    # `max(USERS)` 对字典求最大值时默认比较的是所有 Key（即当前最大 user_id），生成自增主键；若字典为空则从 1 开始
    new_id = max(USERS) + 1 if USERS else 1
    user = {"user_id": new_id, "name": req.name, "email": req.email}
    USERS[new_id] = user
    return user


@app.get("/health")
def health():
    """【业务】：存活探针（Liveness / Readiness Probe），供 K8s、网关或负载均衡定时检测服务是否健康在线。"""
    return {"status": "ok"}


@app.post("/hello")
def hello(req: HelloReq):
    """【业务】：最小 POST 示例——校验名字并打招呼；当名字为 `error` 时演示主动抛出业务异常。"""
    # 这里先复刻你最熟的 Java 接口：把 req 当成 DTO，返回值当成 Response
    if req.name == "error":
        # `HTTPException` 类似 Spring 的业务异常，抛出后会被下方的 `@app.exception_handler(HTTPException)` 统一拦截转换成标准 JSON 错误体
        raise HTTPException(status_code=400, detail="name 不能是 error")
    return {"message": f"你好，{req.name}"}


@app.get("/users/pageList", response_model=list[UserOut])
def get_users(page: int = Query(1, ge=1), size: int = Query(10, ge=1, le=10)):
    """
    【业务】：分页查询用户列表。
    【技术】：
    1. `Query(默认值, 校验条件)` 声明参数来自 URL 查询串（如 `?page=1&size=10`，类似 Spring `@RequestParam`），且限制页码 >=1、每页大小 1~10；
    2. 路由顺序细节：`/users/pageList` 必须写在下面 `/users/{user_id}` 之前（或靠类型区分），否则 `"pageList"` 会被当成 `{user_id}` 去转 `int` 而报 422！
    3. `user_list[start:end]` 是 Python 列表切片语法，越界时不会抛 `IndexOutOfBoundsException`，而是优雅返回空列表 `[]`。
    """
    user_list = list(USERS.values())
    return user_list[(page - 1) * size:page * size]


@app.get("/users/{user_id}", response_model=UserOut)
async def get_user(user_id: int):
    """
    【业务】：按用户 ID 精确查询单个用户，不存在则返回 404。
    【技术】：函数参数名 `user_id` 与路径模板 `{user_id}` 一致（类似 `@PathVariable`），且标注为 `: int`，
    如果前端访问 `/users/abc`，不用进函数体就会被 FastAPI 自动拦截返回 422 参数类型错误。
    """
    # 用 `dict.get(key)` 而不是 `USERS[user_id]`：键不存在时返回 `None` 而非直接抛 `KeyError` 导致 500
    user = USERS.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"user {user_id} not found")
    return user


# ==================== 第三部分：同步 vs 异步并发行为对比实验 ====================
# 【核心设计目的】：直观验证 FastAPI 处理普通 `def` 与协程 `async def` 的底层机制差异（对应 README 里的 5 并发压测结论）：

@app.get("/demo/sync-sleep")
def sync_sleep(seconds: float = 2):
    """
    【场景 1：同步路由 `def` + 同步阻塞 `time.sleep`】
    【为什么 5 并发耗时约 2 秒】：因为没有写 `async`，FastAPI 知道里面可能有阻塞调用（如传统同步 JDBC / requests），
    会自动把它丢进外部线程池（AnyIO Worker ThreadPool）并发执行，5 个请求占 5 个工作线程，互不卡死主事件循环。
    """
    time.sleep(seconds)
    return {"模式": "同步方法睡眠time.sleep", "睡眠时间": seconds}


@app.get("/demo/async-sleep")
async def async_sleep(seconds: float = 2):
    """
    【场景 2（推荐标准写法）：异步路由 `async def` + 非阻塞 `await asyncio.sleep`】
    【为什么 5 并发耗时约 2 秒且只占 1 个线程】：遇到 `await` 时当前协程主动挂起让出主线程控制权，
    单线程事件循环可以同时托管成千上万个等待网络 I/O（如等待大模型吐字）的连接，内存和切换开销远低于多线程。
    """
    await asyncio.sleep(seconds)
    return {"模式": "异步唤醒方法睡眠asyncio.sleep", "睡眠时间": seconds}


@app.get("/demo/async-blocked")
async def async_blocked(seconds: float = 2):
    """
    【场景 3（典型反面教材！千万别在生产这么写）：异步路由 `async def` + 同步阻塞 `time.sleep`】
    【为什么 5 并发会串行排队变成 10 秒】：写了 `async def` 后，FastAPI 信任你不会阻塞，直接在唯一的事件循环主线程上跑它；
    结果 `time.sleep` 把主线程死死卡住 2 秒，期间整个服务连 `/health` 都响应不了，所有并发请求被迫排队串行执行！
    """
    time.sleep(seconds)
    return {"模式": "异步唤醒方法调用同步睡眠time.sleep", "睡眠时间": seconds}


# ==================== 第四部分：全局异常处理器与日志初始化 ====================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    【业务含义】：统一错误响应格式，把 FastAPI 默认的 `{"detail": "..."}` 包装成企业后端常见的 `{"code": "...", "message": "..."}` 结构。
    【技术含义】：类似 Spring 的 `@RestControllerAdvice` + `@ExceptionHandler(HTTPException.class)`，全局拦截所有 `HTTPException`。
    """
    code = {400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found"}.get(exc.status_code, "ERROR")
    return JSONResponse(status_code=exc.status_code, content={"code": code, "message": exc.detail})


# 初始化全局日志配置：
# `getattr(logging, settings.log_level.upper(), logging.INFO)` 动态反射获取 `logging.DEBUG` / `logging.INFO` 常量，配置写错时安全回退到 INFO
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ai-service")
logger.info("ai-service start 已启动，日志等级 log_level = %s", settings.log_level)


# ==================== 第五部分：大模型核心业务接口 ====================

class ChatRequest(BaseModel):
    """通用对话请求体：校验用户输入长度（1~4000 字符防空串或超长攻击），允许前端自定义系统人设与采样温度"""
    message: str = Field(min_length=1, max_length=4000)
    system: str = "你是一个有帮助的助手，用中文回答。"
    temperature: float = 0.7


class ChatResponse(BaseModel):
    """普通对话响应体：除了回复正文，还把模型名、Token 消耗、耗时一并返回，方便前端调试面板展示"""
    reply: str
    model: str
    input_tokens: int
    output_tokens: int
    duration_ms: int


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    """
    【业务】：单轮非流式 AI 对话接口。
    【技术】：组装 OpenAI 标准双角色消息（`system` 设定角色边界和语气，`user` 放本轮用户提问），`await` 异步调用 `llm.chat`。
    """
    messages = [
        {"role": "system", "content": req.system},
        {"role": "user", "content": req.message},
    ]
    return await llm.chat(messages, temperature=req.temperature)


@app.post("/chat/stream")
async def chat_stream_endpoint(req: ChatRequest):
    r"""
    【业务】：SSE（Server-Sent Events）流式 AI 对话接口，实现前端“逐字蹦出”的打字机体验。
    【技术含义 & 为什么这么写】：
    1. SSE 协议格式要求每条消息以 `data: ` 开头、以双换行 `\n\n` 结尾；
    2. 为什么要把 `piece` 包一层 `json.dumps({'content': piece}, ensure_ascii=False)`：
       如果 `piece` 本身含有换行符 `\n`，裸文本直接拼会破坏 SSE 的 `\n\n` 帧边界导致前端解析断行错乱！包成单行 JSON 字符串最稳妥，`ensure_ascii=False` 让中文直接明文输出而不转义成 `\u4f60`；
    3. 最后发一条行业通用的 `data: [DONE]\n\n` 哨兵帧，通知前端流已正常结束；
    4. `StreamingResponse(..., media_type="text/event-stream")` 保持 HTTP 长连接不断开，消费内部异步生成器 `gen()` 边产边推。
    """
    messages = [
        {"role": "system", "content": req.system},
        {"role": "user", "content": req.message},
    ]

    async def gen():
        async for piece in llm.chat_stream(messages, temperature=req.temperature):
            yield f"data: {json.dumps({'content': piece}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


class ExtractRequest(BaseModel):
    """结构化抽取请求体：传入一段待提取的自由文本（如简历片段、自我介绍）"""
    text: str = Field(min_length=1,max_length=2000)


# ⚠️【小提示】：路径写成了 `/exact`（若本意是“抽取 extract”，可能是拼写笔误，但改动路径会影响前端调用约定，此处保持原样）
@app.post("/exact",response_model=ExtractedProfile)
async def extract_endpoint(req: ExtractRequest):
    """
    【业务】：非结构化文本 -> 结构化 JSON 画像抽取接口（典型企业级 AI 落地场景：表单自动填充、简历解析、工单分类）。
    【为什么捕获异常返回 502 Bad Gateway】：
    大模型属于本服务依赖的“上游网关/外部服务”。当模型输出格式崩坏（`JSONDecodeError`）或字段类型不符合 `ExtractedProfile` 契约（`ValidationError`）时，
    既不是客户端传参错（4xx），也不是我方代码空指针（500），按 HTTP 语义返回 `502 Bad Gateway` 最准确。
    """
    messages = [
        {"role": "system", "content": "你是一个信息抽取助手。只返回 JSON，不要加任何解释文字。字段：name（姓名）、age（年龄数字，没有就写 null）、role（当前职业）、years_experience（工作年限数字，没有就写 null）、target_direction（目标方向）。"},
        {"role": "user", "content": req.text},
    ]
    try:
        data = await llm.chat_json(messages,temperature=0.3)
        # `ExtractedProfile(**data)` 将解析出的字典解包传给 Pydantic 模型做二次强类型校验与默认值补全
        return ExtractedProfile(**data)
    except (json.JSONDecodeError,ValidationError) as e:
        raise HTTPException(status_code=502,detail=f"模型输出接口不符：{e}")


class ChatToolsResponse(BaseModel):
    """工具调用对话响应体：除最终回复外，额外返回 `used_tools` 列表，透明展示模型背后调用了哪些工具及参数"""
    reply: str
    used_tools: list[dict] = []


@app.post("/chat/tools", response_model=ChatToolsResponse)
async def chat_tools_endpoint(req: ChatRequest):
    """
    【业务】：支持联网查天气与精确四则运算的智能助手接口。
    【为什么 System Prompt 要强调“不要自己编天气、不要自己心算”】：
    进一步降低模型“幻觉自信（明知有工具却强行靠记忆瞎猜）”的概率，引导模型积极触发 Function Calling。
    """
    messages = [
        {"role": "system", "content": "你是一个有帮助的助手，用中文回答。需要查天气或算数时调用工具，不要自己编天气、不要自己心算。"},
        {"role": "user", "content": req.message},
    ]
    return await llm.chat_with_tools(messages)
