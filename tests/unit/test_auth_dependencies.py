from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.auth.contracts import CurrentUser
from app.auth.dependencies import require_role


@pytest.mark.asyncio
async def test_role_dependency_rejects_a_non_admin() -> None:
    dependency = require_role("admin")

    with pytest.raises(HTTPException) as exc_info:
        await dependency(CurrentUser(user_id=uuid4(), username="operator", role="user"))

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_role_dependency_returns_matching_user() -> None:
    dependency = require_role("admin")
    user = CurrentUser(user_id=uuid4(), username="admin", role="admin")

    assert await dependency(user) == user
