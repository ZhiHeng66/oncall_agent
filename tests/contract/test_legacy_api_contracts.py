import json
from collections.abc import AsyncGenerator
from importlib import import_module
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

chat_api = import_module("app.api.chat")
aiops_api = import_module("app.api.aiops")


class FakeRagAgentService:
    async def query(self, question: str, session_id: str) -> str:
        return f"answer:{session_id}:{question}"

    async def query_stream(
        self, question: str, session_id: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        yield {"type": "content", "data": "hello"}
        yield {"type": "complete", "data": {"answer": "hello"}}

    def clear_session(self, session_id: str) -> bool:
        return True

    def get_session_history(self, session_id: str) -> list[dict[str, str]]:
        return []


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
    monkeypatch.setattr(chat_api, "rag_agent_service", FakeRagAgentService())
    monkeypatch.setattr(aiops_api, "aiops_service", FakeAIOpsService())
    app = FastAPI()
    app.include_router(chat_api.router, prefix="/api")
    app.include_router(aiops_api.router, prefix="/api")
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
