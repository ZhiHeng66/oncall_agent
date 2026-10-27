from importlib import import_module
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.contracts import CurrentUser, UserRole
from app.auth.dependencies import get_current_user

file_api = import_module("app.api.file")


def _client_for(role: UserRole) -> TestClient:
    app = FastAPI()
    app.include_router(file_api.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=UUID("00000000-0000-0000-0000-000000000011"),
        username="test-user",
        role=role,
    )
    return TestClient(app)


def test_upload_and_directory_indexing_require_admin() -> None:
    client = _client_for("user")

    upload = client.post("/api/upload", files={"file": ("note.txt", b"hello", "text/plain")})
    index = client.post("/api/index_directory")

    assert upload.status_code == 403
    assert index.status_code == 403
