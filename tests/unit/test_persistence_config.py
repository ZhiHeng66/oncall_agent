from pathlib import Path

import pytest
from pydantic import ValidationError

from app.cli.migrate_sqlite import migrate_sqlite_run_store
from app.config import Settings
from app.models.agent import AgentRunMetrics, AgentRunResponse
from app.services.persistence import PersistenceFactory
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


def test_working_memory_budgets_must_be_consistent() -> None:
    with pytest.raises(ValidationError, match="SUMMARY_MAX_CHARS"):
        Settings(
            _env_file=None,
            dashscope_api_key="",
            working_memory_context_max_chars=1000,
            working_memory_summary_max_chars=2000,
        )

    with pytest.raises(ValidationError, match="COMPRESSION_THRESHOLD"):
        Settings(
            _env_file=None,
            dashscope_api_key="",
            working_memory_context_max_chars=1000,
            working_memory_summary_max_chars=1000,
            working_memory_compression_threshold=2000,
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


async def test_non_postgres_backends_explicitly_disable_working_memory(tmp_path: Path) -> None:
    for settings in (
        Settings(_env_file=None, dashscope_api_key="", checkpointer_backend="memory"),
        Settings(
            _env_file=None,
            dashscope_api_key="",
            checkpointer_backend="sqlite",
            checkpoint_path=str(tmp_path / "runtime.sqlite3"),
        ),
    ):
        async with PersistenceFactory(settings).open() as persistence:
            assert persistence.working_memory_store is None
