from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings
from app.core.llm_factory import LLMFactory


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
    }
