import asyncio

import pytest

from app.agent.contracts import AgentResult, AgentTask, Evidence, ReviewResult, RouteDecision
from app.agent.graph import MultiAgentGraph
from app.agent.runtime.limits import RuntimeLimits


def limits(
    *,
    reviewer_retries: int = 1,
    supervisor_rounds: int = 4,
    agent_timeout: float = 1.0,
) -> RuntimeLimits:
    return RuntimeLimits(
        supervisor_max_rounds=supervisor_rounds,
        task_max_steps=4,
        reviewer_max_retries=reviewer_retries,
        agent_timeout_seconds=agent_timeout,
        tool_timeout_seconds=1.0,
        tool_max_retries=1,
        graph_recursion_limit=30,
    )


def task_for(target: str, suffix: str = "one") -> AgentTask:
    return AgentTask(task_id=f"{target}-{suffix}", objective=f"run {target}")


def decision(mode: str, targets: list[str], *, answer: str = "") -> RouteDecision:
    return RouteDecision(
        mode=mode,
        target_agents=targets,
        tasks=[task_for(target) for target in targets],
        reason_summary=f"route to {targets}",
        direct_answer=answer,
    )


class SequenceSupervisor:
    def __init__(self, decisions: list[RouteDecision]) -> None:
        self.decisions = decisions
        self.calls = 0

    async def route(self, user_query, previous_results, review, round_number):
        selected = self.decisions[min(self.calls, len(self.decisions) - 1)]
        self.calls += 1
        return selected


class FakeAgent:
    def __init__(self, name: str, *, status: str = "success", errors=None) -> None:
        self.name = name
        self.status = status
        self.errors = errors or []
        self.calls = 0

    async def run(self, task: AgentTask, *, event_sink=None) -> AgentResult:
        self.calls += 1
        source_type = "knowledge" if self.name == "knowledge" else "tool"
        citation = " [来源1]" if self.name == "knowledge" else ""
        return AgentResult(
            agent_name=self.name,
            task_id=task.task_id,
            status=self.status,
            summary=f"{self.name} result{citation}",
            evidence=[Evidence(source_type=source_type, source_name=self.name, content="fact")]
            if self.status != "failed"
            else [],
            errors=self.errors,
        )


class FakeTaskAgent(FakeAgent):
    async def run(self, task: AgentTask, thread_id: str | None = None) -> AgentResult:
        return await super().run(task)

    async def stream(self, task: AgentTask, thread_id: str | None = None):
        result = await self.run(task, thread_id=thread_id)
        yield {"type": "run_completed", "result": result.model_dump(mode="json")}


class TimelineTaskAgent(FakeTaskAgent):
    async def stream(self, task: AgentTask, thread_id: str | None = None):
        yield {"type": "plan_created", "plan": ["collect"]}
        yield {"type": "step_started", "step": "collect", "step_index": 1}
        yield {
            "type": "step_completed",
            "step": "collect",
            "step_index": 1,
            "status": "success",
            "summary": "collected",
        }
        result = await self.run(task, thread_id=thread_id)
        yield {"type": "run_completed", "result": result.model_dump(mode="json")}


class NoopReviewer:
    def __init__(self) -> None:
        self.calls = 0

    async def review(self, user_query, candidate_answer, evidence, errors):
        self.calls += 1
        return ReviewResult(
            passed=True,
            completeness_score=1.0,
            groundedness_score=1.0,
        )


class SequenceReviewer:
    def __init__(self, results: list[ReviewResult]) -> None:
        self.results = results
        self.calls = 0

    async def review(self, user_query, candidate_answer, evidence, errors):
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return result


class ConcurrentAgent(FakeAgent):
    def __init__(self, name: str, shared: dict[str, object]) -> None:
        super().__init__(name)
        self.shared = shared

    async def run(self, task: AgentTask, *, event_sink=None) -> AgentResult:
        self.shared["started"] = int(self.shared["started"]) + 1
        if self.shared["started"] == 2:
            self.shared["both_started"].set()
        await asyncio.wait_for(self.shared["both_started"].wait(), timeout=0.2)
        return await super().run(task, event_sink=event_sink)


class SlowAgent(FakeAgent):
    async def run(self, task: AgentTask, *, event_sink=None) -> AgentResult:
        await asyncio.sleep(0.1)
        return await super().run(task, event_sink=event_sink)


class RaisingSupervisor:
    async def route(self, user_query, previous_results, review, round_number):
        raise RuntimeError("Authorization: Bearer private-supervisor-token")


def graph_for(
    route_decisions: list[RouteDecision],
    *,
    knowledge=None,
    data=None,
    task=None,
    reviewer=None,
    runtime_limits=None,
) -> MultiAgentGraph:
    return MultiAgentGraph(
        supervisor=SequenceSupervisor(route_decisions),
        knowledge=knowledge or FakeAgent("knowledge"),
        data=data or FakeAgent("data"),
        task=task or FakeTaskAgent("task"),
        reviewer=reviewer or NoopReviewer(),
        limits=runtime_limits or limits(),
    )


async def test_direct_route_uses_fast_path_without_specialists_or_reviewer() -> None:
    knowledge = FakeAgent("knowledge")
    data = FakeAgent("data")
    task = FakeTaskAgent("task")
    reviewer = NoopReviewer()
    graph = graph_for(
        [decision("direct", [], answer="你好，有什么可以帮你？")],
        knowledge=knowledge,
        data=data,
        task=task,
        reviewer=reviewer,
    )

    result = await graph.run("你好", user_id="user", thread_id="thread", run_id="direct")

    assert result.status == "completed"
    assert result.final_answer == "你好，有什么可以帮你？"
    assert knowledge.calls == data.calls == task.calls == reviewer.calls == 0
    assert result.termination_reason == "fast_path"


@pytest.mark.parametrize(("target", "mode"), [("knowledge", "single"), ("data", "single")])
async def test_single_route_dispatches_only_selected_specialist(target, mode) -> None:
    knowledge = FakeAgent("knowledge")
    data = FakeAgent("data")
    graph = graph_for(
        [decision(mode, [target])],
        knowledge=knowledge,
        data=data,
    )

    result = await graph.run("single", user_id="user", thread_id="thread", run_id=target)

    assert result.status == "completed"
    assert knowledge.calls == (1 if target == "knowledge" else 0)
    assert data.calls == (1 if target == "data" else 0)
    assert [record.agent_name for record in result.dispatch_records] == [target]


async def test_parallel_route_starts_knowledge_and_data_concurrently() -> None:
    shared = {"started": 0, "both_started": asyncio.Event()}
    graph = graph_for(
        [decision("parallel", ["knowledge", "data"])],
        knowledge=ConcurrentAgent("knowledge", shared),
        data=ConcurrentAgent("data", shared),
    )

    result = await graph.run("parallel", user_id="user", thread_id="thread", run_id="parallel")

    assert result.status == "completed"
    assert {item.agent_name for item in result.agent_results} == {"knowledge", "data"}
    assert len(result.dispatch_records) == 2
    assert len(result.review_history) == 1


async def test_complex_route_dispatches_task_agent() -> None:
    task = FakeTaskAgent("task")
    graph = graph_for([decision("complex", ["task"])], task=task)

    result = await graph.run("complex", user_id="user", thread_id="thread", run_id="complex")

    assert result.status == "completed"
    assert task.calls == 1
    assert result.route is not None and result.route.mode == "complex"
    assert result.termination_reason == "review_passed"


async def test_specialist_failure_is_disclosed_as_partial_without_loop() -> None:
    data = FakeAgent("data", status="failed", errors=["data source unavailable"])
    graph = graph_for(
        [decision("parallel", ["knowledge", "data"])],
        data=data,
    )

    result = await graph.run("partial", user_id="user", thread_id="thread", run_id="partial")

    assert result.status == "partial"
    assert "未完成或错误" in result.final_answer
    assert result.errors == ["data source unavailable"]
    assert len(result.dispatch_records) == 2


def rejected(target: str = "knowledge") -> ReviewResult:
    return ReviewResult(
        passed=False,
        completeness_score=0.5,
        groundedness_score=0.5,
        issues=["需要补充依据"],
        retry_target=target,
        retry_instruction="补充依据",
    )


def passed() -> ReviewResult:
    return ReviewResult(
        passed=True,
        completeness_score=1.0,
        groundedness_score=1.0,
    )


async def test_reviewer_can_request_exactly_one_targeted_retry_then_pass() -> None:
    knowledge = FakeAgent("knowledge")
    reviewer = SequenceReviewer([rejected(), passed()])
    graph = graph_for(
        [
            decision("parallel", ["knowledge", "data"]),
            decision("single", ["knowledge"]),
        ],
        knowledge=knowledge,
        reviewer=reviewer,
    )

    result = await graph.run("retry", user_id="user", thread_id="thread", run_id="retry-pass")

    assert result.status == "completed"
    assert knowledge.calls == 2
    assert reviewer.calls == 2
    assert len(result.review_history) == 2
    assert [record.round for record in result.dispatch_records] == [1, 1, 2]
    assert result.termination_reason == "review_passed"


async def test_second_reviewer_rejection_forces_partial_end() -> None:
    knowledge = FakeAgent("knowledge")
    reviewer = SequenceReviewer([rejected(), rejected()])
    graph = graph_for(
        [
            decision("parallel", ["knowledge", "data"]),
            decision("single", ["knowledge"]),
        ],
        knowledge=knowledge,
        reviewer=reviewer,
    )

    result = await graph.run("retry", user_id="user", thread_id="thread", run_id="retry-stop")

    assert result.status == "partial"
    assert knowledge.calls == 2
    assert reviewer.calls == 2
    assert len(result.review_history) == 2
    assert result.termination_reason == "review_limit"
    assert "质量检查未通过" in result.final_answer


async def test_agent_timeout_becomes_explicit_failed_result() -> None:
    reviewer = SequenceReviewer([rejected("none")])
    graph = graph_for(
        [decision("single", ["data"])],
        data=SlowAgent("data"),
        reviewer=reviewer,
        runtime_limits=limits(agent_timeout=0.01),
    )

    result = await graph.run("timeout", user_id="user", thread_id="thread", run_id="timeout")

    assert result.status == "failed"
    assert result.agent_results[0].status == "failed"
    assert result.termination_reason == "all_specialists_failed"


async def test_same_thread_runs_are_isolated_by_run_id() -> None:
    graph = graph_for([decision("single", ["knowledge"])])

    first = await graph.run("first", user_id="user", thread_id="same", run_id="run-one")
    second = await graph.run("second", user_id="user", thread_id="same", run_id="run-two")

    assert first.run_id == "run-one"
    assert second.run_id == "run-two"
    assert len(first.agent_results) == len(second.agent_results) == 1


async def test_unexpected_graph_failure_returns_clean_failed_terminal() -> None:
    graph = MultiAgentGraph(
        supervisor=RaisingSupervisor(),
        knowledge=FakeAgent("knowledge"),
        data=FakeAgent("data"),
        task=FakeTaskAgent("task"),
        reviewer=NoopReviewer(),
        limits=limits(),
    )

    result = await graph.run("fail", user_id="user", thread_id="thread", run_id="graph-fail")

    assert result.status == "failed"
    assert result.termination_reason == "graph_failed"
    assert "private-supervisor-token" not in result.errors[0]


async def test_top_level_stream_propagates_task_timeline_and_one_graph_terminal() -> None:
    graph = graph_for(
        [decision("complex", ["task"])],
        task=TimelineTaskAgent("task"),
    )

    events = [
        event
        async for event in graph.stream(
            "complex",
            user_id="user",
            thread_id="thread",
            run_id="timeline",
        )
    ]

    event_types = [event["type"] for event in events]
    assert event_types == [
        "route_decided",
        "agent_started",
        "plan_created",
        "step_started",
        "step_completed",
        "agent_completed",
        "review_completed",
        "graph_completed",
    ]
    assert sum(event["type"] == "graph_completed" for event in events) == 1
    assert events[-1]["result"]["status"] == "completed"
