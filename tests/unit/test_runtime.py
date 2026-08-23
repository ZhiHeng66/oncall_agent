import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.agent.runtime.checkpointer import CheckpointerFactory
from app.agent.runtime.limits import RuntimeLimits
from app.config import Settings


def test_checkpointer_factory_returns_isolated_memory_savers() -> None:
    factory = CheckpointerFactory(Settings(_env_file=None, dashscope_api_key=""))

    first = factory.create_memory()
    second = factory.create_memory()

    assert isinstance(first, MemorySaver)
    assert isinstance(second, MemorySaver)
    assert first is not second


async def test_checkpointer_factory_opens_sqlite_backend(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        dashscope_api_key="",
        checkpointer_backend="sqlite",
        checkpoint_path=str(tmp_path / "checkpoints.sqlite3"),
    )

    async with CheckpointerFactory(settings).open() as saver:
        await saver.aget({"configurable": {"thread_id": "thread-1"}})

    assert (tmp_path / "checkpoints.sqlite3").exists()


def test_runtime_limits_are_loaded_from_settings() -> None:
    settings = Settings(
        _env_file=None,
        dashscope_api_key="",
        task_max_steps=6,
        reviewer_max_retries=1,
        agent_timeout_seconds=12.0,
    )

    limits = RuntimeLimits.from_settings(settings)

    assert limits.task_max_steps == 6
    assert limits.reviewer_max_retries == 1
    assert limits.agent_timeout_seconds == 12.0


def test_runtime_limits_reject_more_than_one_reviewer_retry() -> None:
    settings = Settings(
        _env_file=None,
        dashscope_api_key="",
        reviewer_max_retries=2,
    )

    with pytest.raises(ValueError, match="zero or one"):
        RuntimeLimits.from_settings(settings)
