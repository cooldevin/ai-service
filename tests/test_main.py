"""
自动化单元测试与接口集成测试模块（Test Suite）
【业务含义】：验证基础接口（健康检查、用户查询/分页/创建校验）、环境变量配置加载以及大模型对话接口（普通/流式）的正确性。
【技术含义】：基于 `pytest` 测试框架 + FastAPI `TestClient` + `monkeypatch` 动态打桩机制：
  1. 无需真正启动 uvicorn 监听端口，直接在内存进程内模拟完整 HTTP 请求/响应链路；
  2. 测试大模型接口时不发真实网络请求（省钱、极速、无外部依赖），而是替换底层函数模拟返回。
"""

from fastapi.testclient import TestClient

from ai_service.main import app

# 创建基于 ASGI 内存通道的测试客户端（底层封装了 httpx，用法类似 Spring MockMvc / WebTestClient）
client = TestClient(app)


# pytest 约定：所有以 `test_` 开头的函数都会被自动发现并作为独立测试用例执行；直接用原生 `assert` 断言即可。

def test_health():
    """【业务】：验证健康检查接口返回 HTTP 200 且响应体为 `{"status": "ok"}`。"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_user():
    """【业务】：验证按已存在的用户 ID（1）查询用户能正常返回 200。"""
    response = client.get("/users/1")
    assert response.status_code == 200


def test_get_user_page():
    """【业务】：验证用户分页查询接口传合法分页参数时正常返回 200。"""
    response = client.get("/users/pageList?page=1&size=10")
    assert response.status_code == 200


def test_create_user201():
    """
    【业务】：正向用例——传入完全符合校验规则的用户数据（含嵌套地址对象），验证创建成功并返回 201 Created。
    【技术】：`client.post(..., json=request_data)` 会自动把字典序列化成 JSON 并附带 `Content-Type: application/json` 请求头。
    """
    request_data = {"name": "Carol", "age": 28, "email": "carol@example.com", "role": "user",
                    "address": {"city": "杭州", "street": "西湖区文一西路"}}
    response = client.post("/create", json=request_data)
    assert response.status_code == 201


def test_create_user422():
    """
    【业务】：反向参数校验用例——传入不合法的姓名长度（1个字符 < 2）和负数年龄（-1 < 0），
    验证 Pydantic 自动拦截并返回 FastAPI 标准的 `422 Unprocessable Entity` 参数校验失败状态码。
    """
    request_data = {"name": "c", "age": -1, "email": "carol", "role": "user",
                    "address": {"city": "杭州", "street": "西湖区文一西路"}}
    response = client.post("/create", json=request_data)
    assert response.status_code == 422


def test_get_user404():
    """【业务】：反向业务用例——查询不存在的用户 ID（999），验证触发自定义 404 异常响应。"""
    response = client.get("/users/999")
    assert response.status_code == 404


def test_settings_read_env(monkeypatch):
    """
    【业务】：验证配置模块能够优先读取系统环境变量覆盖默认值。
    【技术含义 & 为什么这么写】：
    1. `monkeypatch` 是 pytest 内置夹具（Fixture），写在参数列表里就会由 pytest 自动注入；
       它对环境变量或模块属性的修改只在当前测试函数内生效，函数跑完后自动还原，不会污染其他测试！
    2. 为什么要 `importlib.reload(config)`：`config.py` 顶层的 `settings = Settings()` 在首次导入时就已经执行完了，
       改完环境变量后必须重新加载一次模块，才能触发重新实例化 `Settings()` 读取新的环境变量。
    """
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    import importlib
    import ai_service.config as config
    importlib.reload(config)
    assert config.settings.log_level == "DEBUG"


def test_chat(monkeypatch):
    """
    【业务】：测试 `/chat` 路由层的请求校验、参数透传与响应组装逻辑。
    【为什么用 `monkeypatch.setattr` 替换 `ai_service.llm.chat`】：
    单元测试只测我方 Web 路由代码对不对，不应该真去调阿里云百炼接口（否则没配 Key 会挂、每次跑测试还要花 Token 钱且慢）。
    用 `async def fake_chat` 模拟异步返回固定字典，等同于 Java 单测里的 `@MockBean` / `Mockito.when(...).thenReturn(...)`。
    """
    async def fake_chat(messages, temperature=0.7, max_tokens=1024):
        return {"reply": "你好", "model": "qwen-plus",
                "input_tokens": 10, "output_tokens": 5, "duration_ms": 123}

    monkeypatch.setattr("ai_service.llm.chat", fake_chat)
    resp = client.post("/chat", json={"message": "你好"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "你好"


def test_chat_stream(monkeypatch):
    """
    【业务】：测试 `/chat/stream` 流式路由能否把底层异步生成器吐出的分片正确包装为 SSE（`text/event-stream`）格式返回。
    【技术】：`fake_stream` 使用 `async def` + `yield` 构造假的异步生成器，模拟大模型分两次吐出 `"你"` 和 `"好"`。
    """
    async def fake_stream(messages, temperature=0.7, max_tokens=1024):
        yield "你"
        yield "好"

    monkeypatch.setattr("ai_service.llm.chat_stream", fake_stream)
    resp = client.post("/chat/stream", json={"message": "你好"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert "data:" in resp.text
