from collections.abc import AsyncGenerator
from typing import Any

from app.services.aiops_service import AIOpsService


class FakeTaskAgent:
    async def stream(
        self, task, thread_id: str | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        yield {"type": "plan_created", "plan": ["collect", "summarize"]}
        yield {"type": "step_started", "step": "collect", "step_index": 1}
        yield {
            "type": "step_completed",
            "step": "collect",
            "step_index": 1,
            "status": "success",
            "summary": "collected",
        }
        yield {
            "type": "run_completed",
            "result": {
                "status": "success",
                "summary": "final result",
                "evidence": [],
                "errors": [],
            },
        }


async def test_aiops_execute_maps_task_events_to_legacy_contract() -> None:
    service = AIOpsService(task_runner=FakeTaskAgent())  # type: ignore[arg-type]

    events = [event async for event in service.execute("generic task", "thread-1")]

    assert [event["type"] for event in events] == [
        "plan",
        "status",
        "step_complete",
        "report",
        "complete",
    ]
    assert events[-1]["status"] == "success"


async def test_aiops_diagnose_keeps_legacy_terminal_shape() -> None:
    service = AIOpsService(task_runner=FakeTaskAgent())  # type: ignore[arg-type]

    events = [event async for event in service.diagnose("thread-1")]

    assert events[-1]["type"] == "complete"
    assert events[-1]["stage"] == "diagnosis_complete"
    assert events[-1]["diagnosis"] == {"status": "success", "report": "final result"}
