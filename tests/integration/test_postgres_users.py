import os
from collections.abc import Iterator
from uuid import uuid4

import pytest

from app.agent.runtime.checkpointer import create_postgres_pool
from app.auth.contracts import NewUser
from app.cli.database import upgrade_application_schema
from app.config import Settings
from app.services.user_store import PostgresUserStore

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def postgres_settings() -> Iterator[Settings]:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    settings = Settings(
        _env_file=None,
        dashscope_api_key="",
        auth_enabled=True,
        jwt_secret_key="a" * 48,
        checkpointer_backend="postgres",
        database_url=database_url,
        postgres_pool_min_size=1,
        postgres_pool_max_size=5,
    )
    upgrade_application_schema(settings)
    yield settings


async def test_postgres_user_store_round_trip(postgres_settings: Settings) -> None:
    pool = create_postgres_pool(postgres_settings)
    await pool.open(wait=True)
    try:
        store = PostgresUserStore(pool)
        await store.setup()
        username = f"phase-one-{uuid4().hex}"
        user = await store.create(NewUser(username=username, password_hash="hash"))

        assert await store.get_by_id(user.user_id) == user
        assert await store.get_by_username(username.upper()) == user

        await store.set_active(user.user_id, False)
        assert (await store.get_by_id(user.user_id)).is_active is False
    finally:
        await pool.close()
