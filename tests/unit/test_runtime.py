import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.agent.runtime.checkpointer import CheckpointerFactory
from app.agent.runtime.limits import RuntimeLimits
from app.config import Settings


def test_checkpointer_factory_returns_isolated_memory_savers() -> None:
    factory = CheckpointerFactory(Settings(_env_file=None, dashscope_api_key=""))

    first = factory.create()
    second = factory.create()

    assert isinstance(first, MemorySaver)
    assert isinstance(second, MemorySaver)
    assert first is not second


def test_checkpointer_factory_rejects_unimplemented_backend() -> None:
    settings = Settings(
        _env_file=None,
        dashscope_api_key="",
        checkpointer_backend="sqlite",
    )

    with pytest.raises(ValueError, match="Unsupported checkpointer backend"):
        CheckpointerFactory(settings).create()


def test_runtime_limits_are_loaded_from_settings() -> None:
    settings = Settings(
        _env_file=None,
        dashscope_api_key="",
        task_max_steps=6,
        reviewer_max_retries=1,
    )

    limits = RuntimeLimits.from_settings(settings)

    assert limits.task_max_steps == 6
    assert limits.reviewer_max_retries == 1
