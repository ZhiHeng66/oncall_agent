from typing import Any, cast

import pytest
from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings
from app.core.llm_factory import LLMFactory, LLMRole


def test_llm_factory_uses_role_profile_without_network() -> None:
    captured: dict[str, Any] = {}

    def fake_builder(**kwargs: Any) -> BaseChatModel:
        captured.update(kwargs)
        return cast(BaseChatModel, object())

    settings = Settings(
        _env_file=None,
        dashscope_api_key="",
        specialist_model="fake-specialist",
        agent_temperature=0.0,
        llm_timeout_seconds=12.0,
        llm_max_retries=1,
    )
    factory = LLMFactory(settings=settings, model_builder=fake_builder)

    model = factory.create_chat_model(role="task")

    assert model is not None
    assert captured == {
        "model": "fake-specialist",
        "api_key": "",
        "temperature": 0.0,
        "streaming": False,
        "timeout": 12.0,
        "max_retries": 1,
        "enable_thinking": False,
    }


@pytest.mark.parametrize(
    "role", ["chat", "supervisor", "specialist", "task", "reviewer", "finalizer", "memory"]
)
def test_all_roles_default_to_plus_without_thinking(
    role: LLMRole, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "DASHSCOPE_MODEL",
        "RAG_MODEL",
        "SUPERVISOR_MODEL",
        "SPECIALIST_MODEL",
        "REVIEWER_MODEL",
        "LLM_ENABLE_THINKING",
    ):
        monkeypatch.delenv(name, raising=False)
    captured: dict[str, Any] = {}

    def fake_builder(**kwargs: Any) -> BaseChatModel:
        captured.update(kwargs)
        return cast(BaseChatModel, object())

    settings = Settings(_env_file=None)
    LLMFactory(settings=settings, model_builder=fake_builder).create_chat_model(role=role)

    assert settings.dashscope_model == "qwen-plus"
    assert settings.dashscope_embedding_model == "text-embedding-v4"
    assert captured["model"] == "qwen-plus"
    assert captured["enable_thinking"] is False


def test_thinking_setting_and_explicit_override() -> None:
    captured: dict[str, Any] = {}

    def fake_builder(**kwargs: Any) -> BaseChatModel:
        captured.update(kwargs)
        return cast(BaseChatModel, object())

    factory = LLMFactory(
        settings=Settings(_env_file=None, llm_enable_thinking=True), model_builder=fake_builder
    )
    factory.create_chat_model()
    assert captured["enable_thinking"] is True

    factory.create_chat_model(enable_thinking=False)
    assert captured["enable_thinking"] is False
