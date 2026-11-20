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
