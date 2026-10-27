import json
from importlib import import_module
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.contracts import AgentTask, MultiAgentRunResult, RouteDecision
from app.auth.contracts import CurrentUser
from app.auth.dependencies import get_current_user
from app.services.event_service import AgentEventService
from app.services.multi_agent_service import MultiAgentService

agent_api = import_module("app.api.agent")


class FakeRunner:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []

    @staticmethod
    def result(run_id: str, thread_id: str, mode: str = "direct") -> MultiAgentRunResult:
        targets = {
            "direct": [],
            "single": ["knowledge"],
            "parallel": ["knowledge", "data"],
            "complex": ["task"],
        }[mode]
        route = RouteDecision(
            mode=mode,
            target_agents=targets,
            tasks=[AgentTask(task_id=f"{target}-1", objective="test") for target in targets],
            reason_summary="test route",
            direct_answer="answer" if mode == "direct" else "",
        )
        return MultiAgentRunResult(
            run_id=run_id,
            thread_id=thread_id,
            status="completed",
            final_answer="answer",
            route=route,
            termination_reason="fast_path",
        )

    async def run(self, user_query, *, user_id, thread_id, run_id=None, **kwargs):
        mode = next(
            (item for item in ("single", "parallel", "complex") if item in user_query),
            "direct",
        )
        return self.result(run_id, thread_id, mode)

    async def stream(self, user_query, *, user_id, thread_id, run_id=None, **kwargs):
        yield {"type": "route_decided", "agent": "supervisor", "data": {"mode": "direct"}}
        yield {
            "type": "graph_completed",
            "result": self.result(run_id, thread_id).model_dump(mode="json"),
        }

    async def delete_run_state(self, user_id: str, thread_id: str, run_id: str) -> None:
        self.deleted.append((thread_id, run_id))


def build_client(monkeypatch: Any) -> TestClient:
    service = MultiAgentService(runner=FakeRunner(), events=AgentEventService())
    monkeypatch.setattr(agent_api, "multi_agent_service", service)
    app = FastAPI()
    app.include_router(agent_api.router, prefix="/api")
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


def test_unified_json_run_and_lookup_contract(monkeypatch: Any) -> None:
    client = build_client(monkeypatch)

    response = client.post(
        "/api/agent/run",
        json={"message": "question"},
    )
    payload = response.json()
    lookup = client.get(f"/api/agent/runs/{payload['run_id']}")
    trace = client.get(f"/api/agent/runs/{payload['run_id']}/trace")
    metrics = client.get("/api/agent/metrics")

    assert response.status_code == 200
    assert payload["status"] == "completed"
    assert payload["thread_id"]
    assert payload["answer"]
    assert payload["sources"] == []
    assert lookup.json() == payload
    assert trace.json()["run"] == payload
    assert trace.json()["events"][-1]["type"] == "run_completed"
    assert metrics.json()["total_runs"] == 1


def test_unified_sse_envelope_and_single_terminal_contract(monkeypatch: Any) -> None:
    client = build_client(monkeypatch)

    response = client.post(
        "/api/agent/run_stream",
        json={"message": "question"},
    )
    events = parse_sse(response.text)

    assert response.status_code == 200
    assert events
    assert all(
        {"event_id", "run_id", "thread_id", "type", "timestamp", "data"} <= event.keys()
        for event in events
    )
    assert sum(event["type"] in {"run_completed", "run_failed"} for event in events) == 1
    assert events[-1]["type"] == "run_completed"


def test_thread_lookup_and_delete_contract(monkeypatch: Any) -> None:
    client = build_client(monkeypatch)
    run = client.post(
        "/api/agent/run",
        json={"message": "question"},
    ).json()

    thread = client.get(f"/api/agent/threads/{run['thread_id']}")
    deleted = client.delete(f"/api/agent/threads/{run['thread_id']}")
    missing = client.get(f"/api/agent/runs/{run['run_id']}")

    assert thread.json()["run_ids"] == [run["run_id"]]
    assert deleted.json() == {"thread_id": run["thread_id"], "deleted_runs": 1}
    assert missing.status_code == 404


def test_unified_request_rejects_blank_fields(monkeypatch: Any) -> None:
    client = build_client(monkeypatch)

    response = client.post(
        "/api/agent/run",
        json={"user_id": " ", "message": "question"},
    )

    assert response.status_code == 422


def test_unified_json_entry_covers_all_top_level_route_modes(monkeypatch: Any) -> None:
    client = build_client(monkeypatch)

    modes = []
    for mode in ("direct", "single", "parallel", "complex"):
        response = client.post(
            "/api/agent/run",
            json={"message": mode},
        )
        modes.append(response.json()["route"]["mode"])

    assert modes == ["direct", "single", "parallel", "complex"]


def test_main_application_registers_new_and_compatibility_routes() -> None:
    from app.main import app

    paths = {route.path for route in app.routes}

    assert {
        "/api/agent/run",
        "/api/agent/run_stream",
        "/api/agent/runs/{run_id}/trace",
        "/api/agent/metrics",
    } <= paths
    assert {"/api/chat", "/api/chat_stream", "/api/aiops"} <= paths
    aiops_route = next(route for route in app.routes if route.path == "/api/aiops")
    assert aiops_route.deprecated is True
