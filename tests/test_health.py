from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def test_health_check():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_auth_endpoints_remain_public():
    email = f"public-{uuid4()}@example.com"
    with TestClient(app) as client:
        register_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "full_name": "Public User",
                "password": "secret123",
                "role": "staff",
            },
        )
        assert register_response.status_code == 201

        login_response = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "secret123"},
        )
        assert login_response.status_code == 200
        assert login_response.json()["message"] == "Login successful"


def test_protected_endpoints_require_authentication():
    with TestClient(app) as client:
        chat_response = client.post(
            "/api/v1/chat",
            json={"message": "hello", "session_id": "lock-test"},
        )
        assert chat_response.status_code == 401

        restaurants_response = client.get("/api/v1/restaurants")
        assert restaurants_response.status_code == 401


def test_protected_endpoints_succeed_with_token():
    email = f"authed-{uuid4()}@example.com"
    with TestClient(app) as client:
        reg = client.post(
            "/api/v1/auth/register",
            json={"email": email, "full_name": "Auth User", "password": "password123", "role": "staff"},
        )
        assert reg.status_code == 201
        token = reg.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        me_res = client.get("/api/v1/auth/me", headers=headers)
        assert me_res.status_code == 200
        assert me_res.json()["email"] == email

        rest_res = client.get("/api/v1/restaurants", headers=headers)
        assert rest_res.status_code == 200
