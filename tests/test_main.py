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
