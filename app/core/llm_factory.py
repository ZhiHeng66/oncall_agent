"""Central factory for chat models used by agents and workflow nodes."""

from collections.abc import Callable
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_qwq import ChatQwen

from app.config import Settings, config

LLMRole = Literal["chat", "supervisor", "specialist", "task", "reviewer", "finalizer"]


class LLMFactory:
    """Create role-specific models from application settings.

    ``model_builder`` is injectable so tests never need a provider credential or
    network connection.
    """

    def __init__(
        self,
        settings: Settings = config,
        model_builder: Callable[..., BaseChatModel] = ChatQwen,
    ) -> None:
        self._settings = settings
        self._model_builder = model_builder

    def create_chat_model(
        self,
        role: LLMRole = "chat",
        *,
        model: str | None = None,
        temperature: float | None = None,
        streaming: bool | None = None,
        api_key: str | None = None,
        **overrides: Any,
    ) -> BaseChatModel:
        profiles: dict[LLMRole, tuple[str, float, bool]] = {
            "chat": (
                self._settings.rag_model,
                self._settings.chat_temperature,
                True,
            ),
            "supervisor": (
                self._settings.supervisor_model,
                self._settings.agent_temperature,
                False,
            ),
            "specialist": (
                self._settings.specialist_model,
                self._settings.agent_temperature,
                False,
            ),
            "task": (
                self._settings.specialist_model,
                self._settings.agent_temperature,
                False,
            ),
            "reviewer": (
                self._settings.reviewer_model,
                self._settings.agent_temperature,
                False,
            ),
            "finalizer": (
                self._settings.rag_model,
                self._settings.agent_temperature,
                True,
            ),
        }
        default_model, default_temperature, default_streaming = profiles[role]
        return self._model_builder(
            model=model or default_model,
            api_key=api_key or self._settings.dashscope_api_key,
            temperature=default_temperature if temperature is None else temperature,
            streaming=default_streaming if streaming is None else streaming,
            timeout=self._settings.llm_timeout_seconds,
            max_retries=self._settings.llm_max_retries,
            **overrides,
        )


llm_factory = LLMFactory()
