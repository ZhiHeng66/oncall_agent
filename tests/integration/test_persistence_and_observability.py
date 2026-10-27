from typing import TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from app.agent.contracts import RouteDecision
from app.agent.graph import MultiAgentGraph
from app.agent.runtime.checkpointer import CheckpointerFactory
from app.config import Settings
from app.models.agent import (
    AgentEventEnvelope,
    AgentRunMetrics,
    AgentRunRequest,
    AgentRunResponse,
)
from app.services.event_service import AgentEventService
from app.services.multi_agent_service import MultiAgentService
from app.services.run_store import SqliteRunStore

USER_ID = UUID("00000000-0000-0000-0000-000000000011")


class CounterState(TypedDict):
    count: int


class DirectSupervisor:
    async def route(self, user_query, previous_results, review, round_number, memory_context=""):
        return RouteDecision(
            mode="direct",
            reason_summary="fixed integration route",
            direct_answer="persistent answer",
        )


class PassThroughFinalResponse:
    async def generate(self, user_query, candidate_answer, *, event_sink=None):
        if event_sink is not None:
            event_sink(candidate_answer)
        return candidate_answer


async def test_sqlite_checkpointer_restores_state_after_reopen(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        dashscope_api_key="",
        checkpointer_backend="sqlite",
        checkpoint_path=str(tmp_path / "runtime.sqlite3"),
    )
    workflow = StateGraph(CounterState)
    workflow.add_node("increment", lambda state: {"count": state["count"] + 1})
    workflow.add_edge(START, "increment")
    workflow.add_edge("increment", END)
    graph_config = {"configurable": {"thread_id": "restore-thread"}}

    async with CheckpointerFactory(settings).open() as saver:
        graph = workflow.compile(checkpointer=saver)
        assert (await graph.ainvoke({"count": 1}, graph_config))["count"] == 2

    async with CheckpointerFactory(settings).open() as reopened_saver:
        reopened_graph = workflow.compile(checkpointer=reopened_saver)
        snapshot = await reopened_graph.aget_state(graph_config)

    assert snapshot.values["count"] == 2


async def test_sqlite_run_store_restores_thread_trace_and_metrics(tmp_path) -> None:
    path = str(tmp_path / "runtime.sqlite3")
    store = SqliteRunStore(path)
    await store.setup()
    response = AgentRunResponse(
        run_id="run-1",
        thread_id="thread-1",
        status="partial",
        answer="answer",
        termination_reason="review_limit",
    )
    event = AgentEventService().create(
        run_id="run-1",
        thread_id="thread-1",
        event_type="run_completed",
        data={"status": "partial"},
    )
    run_metrics = AgentRunMetrics(
        duration_ms=25.0,
        dispatch_count=2,
        tool_call_count=1,
        task_step_count=3,
    )
    await store.save(USER_ID, "question", response, [event], run_metrics)

    reopened = SqliteRunStore(path)
    await reopened.setup()
    thread = await reopened.get_thread(USER_ID, "thread-1")
    trace = await reopened.get_trace(USER_ID, "run-1")
    metrics = await reopened.metrics(USER_ID)

    assert thread.run_ids == ["run-1"]
    assert [message["role"] for message in thread.history] == ["user", "assistant"]
    assert trace is not None
    assert trace.run == response
    assert trace.events == [AgentEventEnvelope.model_validate(event)]
    assert trace.metrics == run_metrics
    assert metrics.total_runs == 1
    assert metrics.average_tool_call_count == 1.0


async def test_agent_service_restores_public_thread_after_restart(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        dashscope_api_key="",
        checkpointer_backend="sqlite",
        checkpoint_path=str(tmp_path / "service.sqlite3"),
    )
    factory = CheckpointerFactory(settings)
    first = MultiAgentService(
        runner=MultiAgentGraph(
            supervisor=DirectSupervisor(), final_response=PassThroughFinalResponse()
        ),
        events=AgentEventService(settings),
        settings=settings,
        checkpointers=factory,
    )
    await first.startup()
    response = await first.run(AgentRunRequest(message="hello"), USER_ID)
    await first.shutdown()

    restarted = MultiAgentService(
        runner=MultiAgentGraph(
            supervisor=DirectSupervisor(), final_response=PassThroughFinalResponse()
        ),
        events=AgentEventService(settings),
        settings=settings,
        checkpointers=factory,
    )
    await restarted.startup()
    trace = await restarted.get_trace(USER_ID, response.run_id)
    await restarted.shutdown()

    assert trace is not None
    assert trace.run.run_id == response.run_id


async def test_sqlite_backed_agent_stream_reaches_one_terminal(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        dashscope_api_key="",
        checkpointer_backend="sqlite",
        checkpoint_path=str(tmp_path / "stream.sqlite3"),
    )
    service = MultiAgentService(
        runner=MultiAgentGraph(
            supervisor=DirectSupervisor(), final_response=PassThroughFinalResponse()
        ),
        events=AgentEventService(settings),
        settings=settings,
        checkpointers=CheckpointerFactory(settings),
    )
    await service.startup()
    events = [
        event async for event in service.stream(AgentRunRequest(message="hello"), USER_ID)
    ]
    trace = await service.get_trace(USER_ID, events[0].run_id)
    await service.shutdown()

    assert [event.type for event in events][-1] == "run_completed"
    assert sum(event.type in {"run_completed", "run_failed"} for event in events) == 1
    assert trace is not None
    assert trace.events[-1].type == "run_completed"
