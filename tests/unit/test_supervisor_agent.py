import pytest
from pydantic import ValidationError

from app.agent.contracts import AgentTask, ReviewResult, RouteDecision
from app.agent.supervisor.agent import SupervisorAgent


class RaisingRouter:
    async def decide(self, user_query, previous_results, review, round_number):
        raise RuntimeError("router unavailable")


class UnexpectedRouter:
    async def decide(self, user_query, previous_results, review, round_number):
        raise AssertionError("directed retry must not ask the router")


@pytest.mark.parametrize(
    ("query", "mode", "targets"),
    [
        ("查询知识库制度", "single", ["knowledge"]),
        ("查询当前外部数据", "single", ["data"]),
        ("结合制度和当前外部数据", "parallel", ["knowledge", "data"]),
        ("先查询资料再制定方案", "complex", ["task"]),
        ("你好", "direct", []),
    ],
)
async def test_supervisor_uses_bounded_fallback_routes(query, mode, targets) -> None:
    decision = await SupervisorAgent(router=RaisingRouter()).route(query, [], None, 1)

    assert decision.mode == mode
    assert decision.target_agents == targets


async def test_supervisor_turns_review_feedback_into_directed_retry() -> None:
    review = ReviewResult(
        passed=False,
        completeness_score=0.5,
        groundedness_score=0.5,
        issues=["缺少制度依据"],
        retry_target="knowledge",
        retry_instruction="补充制度证据",
    )

    decision = await SupervisorAgent(router=UnexpectedRouter()).route("原始请求", [], review, 2)

    assert decision.mode == "single"
    assert decision.target_agents == ["knowledge"]
    assert decision.tasks[0].objective == "补充制度证据"
    assert any("不得编造新证据" in item for item in decision.tasks[0].constraints)


@pytest.mark.parametrize(
    "decision",
    [
        {
            "mode": "direct",
            "target_agents": ["knowledge"],
            "tasks": [],
            "reason_summary": "invalid",
        },
        {
            "mode": "single",
            "target_agents": ["task"],
            "tasks": [AgentTask(task_id="one", objective="test")],
            "reason_summary": "invalid",
        },
        {
            "mode": "parallel",
            "target_agents": ["knowledge", "knowledge"],
            "tasks": [
                AgentTask(task_id="one", objective="test"),
                AgentTask(task_id="two", objective="test"),
            ],
            "reason_summary": "invalid",
        },
    ],
)
def test_route_decision_rejects_invalid_graph_shapes(decision) -> None:
    with pytest.raises(ValidationError):
        RouteDecision.model_validate(decision)
