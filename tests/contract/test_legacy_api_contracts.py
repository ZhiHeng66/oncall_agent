import json
from collections.abc import AsyncGenerator
from importlib import import_module
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.contracts import CurrentUser
from app.auth.dependencies import get_current_user
from app.models.agent import (
    AgentRunResponse,
    AgentThreadDeleteResponse,
    AgentThreadResponse,
)
from app.services.event_service import AgentEventService

chat_api = import_module("app.api.chat")
aiops_api = import_module("app.api.aiops")


class FakeMultiAgentService:
    def __init__(self) -> None:
        self.events = AgentEventService()

    async def run(self, request, owner_user_id) -> AgentRunResponse:
        return AgentRunResponse(
            run_id="run-json",
            thread_id=request.thread_id,
            status="completed",
            answer=f"answer:{request.thread_id}:{request.message}",
            termination_reason="fast_path",
        )

    async def stream(self, request, owner_user_id):
        yield self.events.create(
            run_id="run-stream",
            thread_id=request.thread_id,
            event_type="content_delta",
            data={"content": "hello"},
        )
        yield self.events.create(
            run_id="run-stream",
            thread_id=request.thread_id,
            event_type="run_completed",
            data={
                "result": AgentRunResponse(
                    run_id="run-stream",
                    thread_id=request.thread_id,
                    status="completed",
                    answer="hello",
                    termination_reason="fast_path",
                ).model_dump(mode="json")
            },
        )

    async def clear_thread(self, thread_id: str, owner_user_id) -> AgentThreadDeleteResponse:
        return AgentThreadDeleteResponse(thread_id=thread_id, deleted_runs=1)

    async def get_thread(self, thread_id: str, owner_user_id) -> AgentThreadResponse:
        return AgentThreadResponse(thread_id=thread_id)


class FakeAIOpsService:
    async def diagnose(self, session_id: str) -> AsyncGenerator[dict[str, Any], None]:
        yield {
            "type": "plan",
            "stage": "plan_created",
            "message": "执行计划已制定，共 1 个步骤",
            "plan": ["collect evidence"],
        }
        yield {
            "type": "complete",
            "stage": "diagnosis_complete",
            "message": "诊断流程完成",
            "diagnosis": {"status": "completed", "report": "done"},
        }


def build_client(monkeypatch: Any) -> TestClient:
    monkeypatch.setattr(chat_api, "multi_agent_service", FakeMultiAgentService())
    monkeypatch.setattr(aiops_api, "aiops_service", FakeAIOpsService())
    app = FastAPI()
    app.include_router(chat_api.router, prefix="/api")
    app.include_router(aiops_api.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=UUID("00000000-0000-0000-0000-000000000011"),
        username="test-user",
        role="user",
    )
    return TestClient(app)


def parse_sse(response_text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response_text.splitlines()
        if line.startswith("data: ")
    ]


def test_chat_json_contract_accepts_legacy_aliases(monkeypatch: Any) -> None:
    client = build_client(monkeypatch)

    response = client.post(
        "/api/chat",
        json={"Id": "thread-1", "Question": "hello"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "message": "success",
        "data": {
            "success": True,
            "answer": "answer:thread-1:hello",
            "errorMessage": None,
        },
    }


def test_chat_stream_keeps_content_and_done_events(monkeypatch: Any) -> None:
    client = build_client(monkeypatch)

    response = client.post(
        "/api/chat_stream",
        json={"Id": "thread-1", "Question": "hello"},
    )
    events = parse_sse(response.text)

    assert response.status_code == 200
    assert [event["type"] for event in events] == ["content", "done"]
    assert events[0]["data"] == "hello"
    assert events[1]["data"] == {"answer": "hello"}


def test_aiops_stream_keeps_plan_and_single_terminal_event(monkeypatch: Any) -> None:
    client = build_client(monkeypatch)

    response = client.post("/api/aiops", json={"session_id": "thread-1"})
    events = parse_sse(response.text)

    assert response.status_code == 200
    assert [event["type"] for event in events] == ["plan", "complete"]
    assert events[0]["plan"] == ["collect evidence"]
    assert sum(event["type"] in {"complete", "error"} for event in events) == 1
