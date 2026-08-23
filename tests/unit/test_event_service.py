from app.config import Settings
from app.services.event_service import AgentEventService


def test_event_envelope_has_required_fields_and_redacts_nested_secrets() -> None:
    service = AgentEventService(
        Settings(
            _env_file=None,
            dashscope_api_key="",
            api_event_data_max_chars=30,
        )
    )

    event = service.create(
        run_id="run-1",
        thread_id="thread-1",
        event_type="agent_completed",
        agent="data",
        data={
            "authorization": "Bearer private-token",
            "nested": {"api_key": "private-key"},
            "summary": "x" * 100,
        },
    )

    payload = event.model_dump(mode="json")
    assert payload["event_id"].startswith("evt-")
    assert payload["run_id"] == "run-1"
    assert payload["thread_id"] == "thread-1"
    assert payload["type"] == "agent_completed"
    assert payload["timestamp"]
    assert payload["data"]["authorization"] == "[REDACTED]"
    assert payload["data"]["nested"]["api_key"] == "[REDACTED]"
    assert len(payload["data"]["summary"]) == 30


def test_content_delta_preserves_whitespace_and_redacts_credentials() -> None:
    service = AgentEventService(Settings(_env_file=None, dashscope_api_key=""))

    event = service.create(
        run_id="run-1",
        thread_id="thread-1",
        event_type="content_delta",
        data={"content": "\n answer Authorization: Bearer private-token "},
    )

    assert event.data["content"] == "\n answer Authorization: [REDACTED] "
