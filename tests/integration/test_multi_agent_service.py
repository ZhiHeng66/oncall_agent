import asyncio

from app.agent.contracts import (
    AgentResult,
    AgentTask,
    Evidence,
    MultiAgentRunResult,
    RouteDecision,
)
from app.config import Settings
from app.models.agent import AgentRunRequest
from app.services.event_service import AgentEventService
from app.services.multi_agent_service import MultiAgentService


def successful_result(run_id: str, thread_id: str) -> MultiAgentRunResult:
    return MultiAgentRunResult(
        run_id=run_id,
        thread_id=thread_id,
        status="completed",
        final_answer="grounded answer token=private-answer-token [来源1]",
        route=RouteDecision(
            mode="single",
            target_agents=["knowledge"],
            tasks=[AgentTask(task_id="knowledge-1", objective="lookup")],
            reason_summary="knowledge request",
        ),
        agent_results=[
            AgentResult(
                agent_name="knowledge",
                task_id="knowledge-1",
                status="success",
                summary="answer [来源1]",
                evidence=[
                    Evidence(
                        source_type="knowledge",
                        source_name="policy.md",
                        source_id="chunk-1",
                        content="token=private-source-token policy fact",
                        metadata={
                            "title": "Policy",
                            "api_key": "private-key",
                            "internal_path": "/private/path",
                        },
                    )
                ],
            )
        ],
        termination_reason="fast_path",
    )


class FakeRunner:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []

    async def run(self, user_query, *, user_id, thread_id, run_id=None):
        return successful_result(run_id, thread_id)

    async def stream(self, user_query, *, user_id, thread_id, run_id=None):
        yield {
            "type": "route_decided",
            "agent": "supervisor",
            "data": {"mode": "single", "authorization": "Bearer private"},
        }
        yield {"type": "agent_started", "agent": "knowledge", "data": {"task_id": "one"}}
        yield {
            "type": "tool_started",
            "agent": "data",
            "data": {"tool_name": "lookup", "status": "running"},
        }
        yield {
            "type": "tool_completed",
            "agent": "data",
            "data": {"tool_name": "lookup", "status": "success", "duration_ms": 1.0},
        }
        yield {
            "type": "agent_completed",
            "agent": "knowledge",
            "data": {"task_id": "one", "status": "success"},
        }
        yield {"type": "final_content_delta", "data": {"content": "grounded answer "}}
        yield {
            "type": "final_content_delta",
            "data": {"content": "token=private-answer-token [来源1]"},
        }
        yield {
            "type": "graph_completed",
            "result": successful_result(run_id, thread_id).model_dump(mode="json"),
        }

    async def delete_run_state(self, thread_id: str, run_id: str) -> None:
        self.deleted.append((thread_id, run_id))


class RaisingStreamRunner(FakeRunner):
    async def stream(self, user_query, *, user_id, thread_id, run_id=None):
        if False:
            yield {}
        raise RuntimeError("Authorization: Bearer private-stream-token")


class CancellableRunner(FakeRunner):
    def __init__(self) -> None:
        super().__init__()
        self.closed = asyncio.Event()

    async def stream(self, user_query, *, user_id, thread_id, run_id=None):
        try:
            yield {"type": "agent_started", "agent": "data", "data": {}}
            await asyncio.Event().wait()
        finally:
            self.closed.set()


def make_service(runner) -> MultiAgentService:
    settings = Settings(_env_file=None, dashscope_api_key="", api_source_excerpt_max_chars=80)
    return MultiAgentService(
        runner=runner,
        events=AgentEventService(settings),
        settings=settings,
    )


class FailingStore:
    async def save(self, request_message, response, events, metrics) -> None:
        raise RuntimeError("database unavailable")

    async def setup(self) -> None:
        return None

    async def get_run(self, run_id):
        return None

    async def get_trace(self, run_id):
        return None

    async def get_thread(self, thread_id):
        raise NotImplementedError

    async def delete_thread(self, thread_id):
        return []

    async def metrics(self):
        raise NotImplementedError


async def test_json_run_standardizes_sources_and_keeps_in_memory_lookup() -> None:
    runner = FakeRunner()
    service = make_service(runner)
    request = AgentRunRequest(user_id="user", thread_id="thread", message="question")

    response = await service.run(request)
    stored = await service.get_run(response.run_id)
    trace = await service.get_trace(response.run_id)
    thread = await service.get_thread("thread")

    assert response.status == "completed"
    assert "private-answer-token" not in response.answer
    assert response.agents == ["supervisor", "knowledge"]
    assert response.sources[0].excerpt == "token=[REDACTED] policy fact"
    assert response.sources[0].metadata == {"title": "Policy"}
    assert stored == response
    assert trace is not None
    assert trace.events[-1].type == "run_completed"
    assert trace.metrics.failed is False
    assert thread.run_ids == [response.run_id]
    assert [item["role"] for item in thread.history] == ["user", "assistant"]


async def test_stream_uses_one_envelope_contract_and_exactly_one_terminal() -> None:
    service = make_service(FakeRunner())
    request = AgentRunRequest(user_id="user", thread_id="thread", message="question")

    events = [event async for event in service.stream(request)]

    assert [event.type for event in events] == [
        "run_started",
        "route_decided",
        "agent_started",
        "tool_started",
        "tool_completed",
        "agent_completed",
        "content_delta",
        "content_delta",
        "run_completed",
    ]
    assert len({event.run_id for event in events}) == 1
    assert all(event.thread_id == "thread" and event.timestamp for event in events)
    assert sum(event.type in {"run_completed", "run_failed"} for event in events) == 1
    assert events[1].data["authorization"] == "[REDACTED]"
    assert (
        "".join(event.data["content"] for event in events if event.type == "content_delta")
        == (events[-1].data["result"]["answer"])
    )
    trace = await service.get_trace(events[0].run_id)
    assert trace is not None
    assert [event.type for event in trace.events] == [event.type for event in events]
    assert trace.metrics.tool_call_count == 1


async def test_stream_exception_emits_one_clean_failed_terminal() -> None:
    service = make_service(RaisingStreamRunner())
    request = AgentRunRequest(user_id="user", thread_id="thread", message="question")

    events = [event async for event in service.stream(request)]

    assert [event.type for event in events] == ["run_started", "run_failed"]
    assert "private-stream-token" not in events[-1].data["error"]


async def test_stream_persistence_failure_still_emits_one_failed_terminal() -> None:
    settings = Settings(_env_file=None, dashscope_api_key="")
    service = MultiAgentService(
        runner=FakeRunner(),
        events=AgentEventService(settings),
        settings=settings,
        store=FailingStore(),
    )

    events = [
        event
        async for event in service.stream(
            AgentRunRequest(user_id="user", thread_id="thread", message="question")
        )
    ]

    assert events[-1].type == "run_failed"
    assert sum(event.type in {"run_completed", "run_failed"} for event in events) == 1
    assert events[-1].data["error"] == "database unavailable"


async def test_closing_client_stream_cancels_underlying_runner() -> None:
    runner = CancellableRunner()
    stream = make_service(runner).stream(
        AgentRunRequest(user_id="user", thread_id="thread", message="question")
    )

    assert (await anext(stream)).type == "run_started"
    assert (await anext(stream)).type == "agent_started"
    await stream.aclose()

    await asyncio.wait_for(runner.closed.wait(), timeout=0.2)


async def test_delete_thread_removes_visible_runs_and_checkpoint_state() -> None:
    runner = FakeRunner()
    service = make_service(runner)
    response = await service.run(
        AgentRunRequest(user_id="user", thread_id="thread", message="question")
    )

    deleted = await service.clear_thread("thread")

    assert deleted.deleted_runs == 1
    assert await service.get_run(response.run_id) is None
    assert (await service.get_thread("thread")).run_ids == []
    assert runner.deleted == [("thread", response.run_id)]
