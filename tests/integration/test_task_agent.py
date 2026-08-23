import pytest

from app.agent.contracts import AgentTask, TaskStepResult
from app.agent.runtime.limits import RuntimeLimits
from app.agent.specialists.task.graph import TaskAgent
from app.agent.specialists.task.schemas import ReplanDecision


class FakePlanner:
    def __init__(self, steps: list[str]) -> None:
        self.steps = steps

    async def plan(self, task: AgentTask) -> list[str]:
        return self.steps


class FakeExecutor:
    async def execute(
        self,
        task: AgentTask,
        step: str,
        past_steps: list[TaskStepResult],
    ) -> TaskStepResult:
        return TaskStepResult(
            step=step,
            status="success",
            summary=f"completed:{step}",
            delegated_agent="direct",
        )


class PartiallyFailingExecutor:
    async def execute(
        self,
        task: AgentTask,
        step: str,
        past_steps: list[TaskStepResult],
    ) -> TaskStepResult:
        failed = step == "fail"
        return TaskStepResult(
            step=step,
            status="failed" if failed else "success",
            summary=f"result:{step}",
            delegated_agent="data",
            errors=["source unavailable"] if failed else [],
        )


class RaisingExecutor:
    async def execute(
        self,
        task: AgentTask,
        step: str,
        past_steps: list[TaskStepResult],
    ) -> TaskStepResult:
        raise RuntimeError("Authorization: Bearer private-token executor failed")


class FakeReplanner:
    async def decide(
        self,
        task: AgentTask,
        remaining_steps: list[str],
        past_steps: list[TaskStepResult],
        remaining_budget: int,
    ) -> ReplanDecision:
        if remaining_steps:
            return ReplanDecision(action="continue")
        return ReplanDecision(action="respond", response=f"完成 {len(past_steps)} 个步骤")


class RaisingReplanner:
    async def decide(
        self,
        task: AgentTask,
        remaining_steps: list[str],
        past_steps: list[TaskStepResult],
        remaining_budget: int,
    ) -> ReplanDecision:
        raise RuntimeError("replanner unavailable")


def limits(max_steps: int = 4) -> RuntimeLimits:
    return RuntimeLimits(
        supervisor_max_rounds=4,
        task_max_steps=max_steps,
        reviewer_max_retries=1,
        agent_timeout_seconds=2.0,
        tool_timeout_seconds=1.0,
        tool_max_retries=1,
        graph_recursion_limit=20,
    )


@pytest.mark.parametrize(
    ("objective", "step"),
    [
        ("整理产品发布检查清单", "查询发布规范并整理清单"),
        ("分析客户反馈并给出改进建议", "汇总客户反馈"),
        ("比较两个交付方案", "收集并比较方案约束"),
    ],
)
async def test_task_agent_runs_non_aiops_tasks(objective: str, step: str) -> None:
    agent = TaskAgent(
        planner=FakePlanner([step]),
        executor=FakeExecutor(),
        replanner=FakeReplanner(),
        limits=limits(),
    )
    task = AgentTask(task_id=objective, objective=objective)

    result = await agent.run(task)

    assert result.status == "success"
    assert result.summary == "完成 1 个步骤"
    assert result.artifacts[0]["termination_reason"] == "responded"


async def test_task_agent_stream_emits_step_lifecycle_and_one_terminal_event() -> None:
    agent = TaskAgent(
        planner=FakePlanner(["step one"]),
        executor=FakeExecutor(),
        replanner=FakeReplanner(),
        limits=limits(),
    )
    task = AgentTask(task_id="stream-1", objective="stream task")

    events = [event async for event in agent.stream(task)]

    assert [event["type"] for event in events] == [
        "plan_created",
        "step_started",
        "step_completed",
        "run_completed",
    ]
    assert sum(event["type"] in {"run_completed", "run_failed"} for event in events) == 1


async def test_task_agent_returns_partial_at_max_steps() -> None:
    agent = TaskAgent(
        planner=FakePlanner(["one", "two", "three"]),
        executor=FakeExecutor(),
        replanner=FakeReplanner(),
        limits=limits(max_steps=2),
    )
    task = AgentTask(task_id="limited", objective="bounded task")

    result = await agent.run(task)

    assert result.status == "partial"
    assert "最大步骤数" in result.summary
    assert len(result.artifacts[0]["steps"]) == 2
    assert result.artifacts[0]["termination_reason"] == "max_steps"


async def test_task_agent_preserves_partial_failure_in_final_result() -> None:
    agent = TaskAgent(
        planner=FakePlanner(["fail", "succeed"]),
        executor=PartiallyFailingExecutor(),
        replanner=FakeReplanner(),
        limits=limits(),
    )
    task = AgentTask(task_id="partial", objective="partially available task")

    result = await agent.run(task)

    assert result.status == "partial"
    assert result.errors == ["source unavailable"]
    assert [step["status"] for step in result.artifacts[0]["steps"]] == [
        "failed",
        "success",
    ]


async def test_task_agent_failed_step_stream_has_explicit_failed_terminal() -> None:
    agent = TaskAgent(
        planner=FakePlanner(["fail"]),
        executor=PartiallyFailingExecutor(),
        replanner=FakeReplanner(),
        limits=limits(),
    )
    task = AgentTask(task_id="failed-stream", objective="failing task")

    events = [event async for event in agent.stream(task)]

    assert [event["type"] for event in events] == [
        "plan_created",
        "step_started",
        "step_failed",
        "run_failed",
    ]
    assert events[-1]["result"]["status"] == "failed"


async def test_task_agent_empty_plan_has_explicit_failed_terminal() -> None:
    agent = TaskAgent(
        planner=FakePlanner([]),
        executor=FakeExecutor(),
        replanner=FakeReplanner(),
        limits=limits(),
    )
    task = AgentTask(task_id="empty-plan", objective="unplannable task")

    events = [event async for event in agent.stream(task)]

    assert [event["type"] for event in events] == ["run_failed"]
    assert events[0]["result"]["status"] == "failed"
    assert events[0]["result"]["artifacts"][0]["termination_reason"] == "empty_plan"


async def test_task_agent_converts_executor_exception_to_clean_failed_step() -> None:
    agent = TaskAgent(
        planner=FakePlanner(["explode"]),
        executor=RaisingExecutor(),
        replanner=FakeReplanner(),
        limits=limits(),
    )

    result = await agent.run(AgentTask(task_id="executor-error", objective="test failure"))

    assert result.status == "failed"
    assert "private-token" not in result.summary
    assert "private-token" not in result.errors[0]


async def test_task_agent_returns_partial_when_replanner_fails_after_success() -> None:
    agent = TaskAgent(
        planner=FakePlanner(["completed step"]),
        executor=FakeExecutor(),
        replanner=RaisingReplanner(),
        limits=limits(),
    )

    result = await agent.run(AgentTask(task_id="replan-error", objective="test replan failure"))

    assert result.status == "partial"
    assert result.errors == ["replanner unavailable"]
    assert result.artifacts[0]["termination_reason"] == "replanner_failed"
