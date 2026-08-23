"""Create safe, typed event envelopes for SSE consumers."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from app.agent.mcp_client import sanitize_error_message
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
    ) -> AgentEventEnvelope:
        return AgentEventEnvelope(
            event_id=f"evt-{uuid4()}",
            run_id=run_id,
            thread_id=thread_id,
            type=event_type,
            timestamp=datetime.now(UTC),
            agent=agent,
            data=self._sanitize_data(data or {}),
        )

    def _sanitize_data(self, data: dict[str, Any]) -> dict[str, Any]:
        cleaned = redact_sensitive_data(data)
        return cast(dict[str, Any], self._bound_value(cleaned))

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
