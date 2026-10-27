import asyncio
from importlib import import_module
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.service import AuthenticationService
from app.config import Settings
from app.services.user_store import InMemoryUserStore

auth_api = import_module("app.api.auth")


def build_client() -> tuple[TestClient, AuthenticationService]:
    settings = Settings(
        _env_file=None,
        auth_enabled=True,
        jwt_secret_key="a" * 48,
        checkpointer_backend="memory",
    )
    service = AuthenticationService(settings=settings, users=InMemoryUserStore())
    asyncio.run(service.create_user("alice", "correct horse battery staple"))
    app = FastAPI()
    app.state.authentication_service = service
    app.include_router(auth_api.router, prefix="/api")
    return TestClient(app), service


def test_token_and_current_user_contract() -> None:
    client, _ = build_client()

    token_response = client.post(
        "/api/auth/token",
        data={"username": "alice", "password": "correct horse battery staple"},
    )
    token_payload = token_response.json()
    me_response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token_payload['access_token']}"},
    )

    assert token_response.status_code == 200
    assert token_payload["token_type"] == "bearer"
    assert token_payload["expires_in"] == 1800
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "alice"
    assert "password_hash" not in me_response.text


def test_invalid_and_disabled_users_are_rejected() -> None:
    client, service = build_client()

    invalid = client.post(
        "/api/auth/token",
        data={"username": "alice", "password": "wrong password"},
    )
    token = client.post(
        "/api/auth/token",
        data={"username": "alice", "password": "correct horse battery staple"},
    ).json()["access_token"]
    user_id = UUID(
        client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["user_id"]
    )
    asyncio.run(service.set_active(user_id, False))
    disabled = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "Incorrect username or password"
    assert disabled.status_code == 401


def test_missing_token_is_rejected() -> None:
    client, _ = build_client()

    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
