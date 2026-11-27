from fastapi.testclient import TestClient

from ai_service.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_user():
    response = client.get("/users/1")
    assert response.status_code == 200


def test_get_user_page():
    response = client.get("/users/pageList?page=1&size=10")
    assert response.status_code == 200


def test_create_user201():
    request_data = {"name": "Carol", "age": 28, "email": "carol@example.com", "role": "user",
                    "address": {"city": "杭州", "street": "西湖区文一西路"}}
    response = client.post("/create", json=request_data)
    assert response.status_code == 201


def test_create_user422():
    request_data = {"name": "c", "age": -1, "email": "carol", "role": "user",
                    "address": {"city": "杭州", "street": "西湖区文一西路"}}
    response = client.post("/create", json=request_data)
    assert response.status_code == 422


def test_get_user404():
    response = client.get("/users/999")
    assert response.status_code == 404


def test_settings_read_env(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    import importlib
    import ai_service.config as config
    importlib.reload(config)
    assert config.settings.log_level == "DEBUG"


def test_chat(monkeypatch):
    async def fake_chat(messages, temperature=0.7, max_tokens=1024):
        return {"reply": "你好", "model": "qwen-plus",
                "input_tokens": 10, "output_tokens": 5, "duration_ms": 123}

    monkeypatch.setattr("ai_service.llm.chat", fake_chat)
    resp = client.post("/chat", json={"message": "你好"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "你好"


def test_chat_stream(monkeypatch):
    async def fake_stream(messages, temperature=0.7, max_tokens=1024):
        yield "你"
        yield "好"

    monkeypatch.setattr("ai_service.llm.chat_stream", fake_stream)
    resp = client.post("/chat/stream", json={"message": "你好"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert "data:" in resp.text
