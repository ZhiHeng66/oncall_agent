"""Create safe, typed event envelopes for SSE consumers."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from app.agent.mcp_client import redact_sensitive_text, sanitize_error_message
from app.agent.specialists.data.utils import redact_sensitive_data
from app.config import Settings, config
from app.models.agent import AgentEventEnvelope, AgentEventType, PublicAgentName


class AgentEventService:
    def __init__(self, settings: Settings = config) -> None:
        self._settings = settings

    def create(
        self,
        *,
        run_id: str,
        thread_id: str,
        event_type: AgentEventType,
        data: dict[str, Any] | None = None,
        agent: PublicAgentName | None = None,
        node: str | None = None,
    ) -> AgentEventEnvelope:
        payload = data or {}
        if event_type == "content_delta" and isinstance(payload.get("content"), str):
            payload = {**payload, "content": self._sanitize_stream_text(payload["content"])}
        else:
            payload = self._sanitize_data(payload)
        return AgentEventEnvelope(
            event_id=f"evt-{uuid4()}",
            run_id=run_id,
            thread_id=thread_id,
            type=event_type,
            timestamp=datetime.now(UTC),
            agent=agent,
            node=node or self._default_node(event_type, agent),
            data=payload,
        )

    @staticmethod
    def _default_node(
        event_type: AgentEventType,
        agent: PublicAgentName | None,
    ) -> str | None:
        if agent in {"knowledge", "data", "task"}:
            return f"{agent}_agent"
        if agent:
            return agent
        if event_type in {"run_started", "run_completed", "run_failed", "content_delta"}:
            return "api_service"
        return None

    def _sanitize_data(self, data: dict[str, Any]) -> dict[str, Any]:
        cleaned = redact_sensitive_data(data)
        return cast(dict[str, Any], self._bound_value(cleaned))

    def _sanitize_stream_text(self, value: str) -> str:
        """Redact a token without removing whitespace needed when chunks are joined."""

        max_chars = self._settings.api_event_data_max_chars
        return redact_sensitive_text(value[:max_chars])

    def _bound_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): self._bound_value(child) for key, child in list(value.items())[:50]}
        if isinstance(value, list):
            return [self._bound_value(child) for child in value[:50]]
        if isinstance(value, str):
            return sanitize_error_message(
                value,
                max_length=self._settings.api_event_data_max_chars,
            )
        return value


agent_event_service = AgentEventService()
