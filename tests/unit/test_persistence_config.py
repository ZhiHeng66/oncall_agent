from pathlib import Path

import pytest
from pydantic import ValidationError

from app.cli.migrate_sqlite import migrate_sqlite_run_store
from app.config import Settings
from app.models.agent import AgentRunMetrics, AgentRunResponse
from app.services.run_store import MemoryRunStore, SqliteRunStore


def test_postgres_backend_requires_database_url() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL is required"):
        Settings(
            _env_file=None,
            dashscope_api_key="",
            checkpointer_backend="postgres",
            database_url="",
        )


def test_postgres_pool_maximum_must_cover_minimum() -> None:
    with pytest.raises(ValidationError, match="POSTGRES_POOL_MAX_SIZE"):
        Settings(
            _env_file=None,
            dashscope_api_key="",
            postgres_pool_min_size=3,
            postgres_pool_max_size=2,
        )


async def test_sqlite_run_store_migration_is_validated_and_idempotent(tmp_path: Path) -> None:
    source = SqliteRunStore(str(tmp_path / "source.sqlite3"))
    await source.setup()
    response = AgentRunResponse(
        run_id="migration-run",
        thread_id="migration-thread",
        status="completed",
        answer="migrated answer",
        termination_reason="completed",
    )
    metrics = AgentRunMetrics(duration_ms=12.0, dispatch_count=1)
    await source.save("migrated question", response, [], metrics)
    target = MemoryRunStore()

    first = await migrate_sqlite_run_store(tmp_path / "source.sqlite3", target)
    second = await migrate_sqlite_run_store(tmp_path / "source.sqlite3", target)

    assert first.runs == second.runs == 1
    assert first.events == second.events == 0
    assert await target.get_run("migration-run") == response
    assert (await target.metrics()).total_runs == 1
