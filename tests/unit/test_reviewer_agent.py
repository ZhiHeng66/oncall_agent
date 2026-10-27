import pytest
from pydantic import ValidationError

from app.agent.contracts import Evidence, ReviewResult
from app.agent.specialists.reviewer.agent import ReviewerAgent


class PassingEvaluator:
    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, user_query, candidate_answer, evidence, errors, memory_context=""):
        self.calls += 1
        return ReviewResult(
            passed=True,
            completeness_score=1.0,
            groundedness_score=1.0,
        )


async def test_reviewer_rejects_unsupported_numeric_claim_without_calling_llm() -> None:
    evaluator = PassingEvaluator()
    reviewer = ReviewerAgent(evaluator=evaluator)

    result = await reviewer.review("增长情况", "收入增长 85%", [], [])

    assert result.passed is False
    assert result.retry_target == "knowledge"
    assert evaluator.calls == 0


async def test_reviewer_rejects_omitted_recorded_error() -> None:
    result = await ReviewerAgent(evaluator=PassingEvaluator()).review(
        "查询数据",
        "数据查询已经完成。",
        [Evidence(source_type="tool", source_name="source", content="available")],
        ["第二个工具调用失败"],
    )

    assert result.passed is False
    assert result.retry_target == "supervisor"
    assert any("没有披露" in issue for issue in result.issues)


async def test_reviewer_rejects_missing_knowledge_citation() -> None:
    result = await ReviewerAgent(evaluator=PassingEvaluator()).review(
        "制度是什么",
        "制度要求完成审批。",
        [Evidence(source_type="knowledge", source_name="policy", content="需要审批")],
        [],
    )

    assert result.passed is False
    assert result.retry_target == "knowledge"


async def test_reviewer_rejects_citation_outside_available_evidence() -> None:
    result = await ReviewerAgent(evaluator=PassingEvaluator()).review(
        "制度是什么",
        "制度要求完成审批。[来源9]",
        [Evidence(source_type="knowledge", source_name="policy", content="需要审批")],
        [],
    )

    assert result.passed is False
    assert any("有效引用" in issue for issue in result.issues)


async def test_reviewer_delegates_semantic_review_when_guards_pass() -> None:
    evaluator = PassingEvaluator()
    result = await ReviewerAgent(evaluator=evaluator).review(
        "解释结果",
        "根据查询结果，当前没有异常。",
        [Evidence(source_type="tool", source_name="metrics", content="normal")],
        [],
    )

    assert result.passed is True
    assert evaluator.calls == 1


def test_passed_review_cannot_request_retry_or_contain_issues() -> None:
    with pytest.raises(ValidationError):
        ReviewResult(
            passed=True,
            completeness_score=1.0,
            groundedness_score=1.0,
            issues=["contradiction"],
            retry_target="knowledge",
        )
