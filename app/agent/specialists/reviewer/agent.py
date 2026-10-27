"""Structured, evidence-bound Reviewer Agent."""

import re
from typing import Protocol

from langchain_core.language_models.chat_models import BaseChatModel

from app.agent.contracts import Evidence, RetryTarget, ReviewResult
from app.agent.specialists.reviewer.prompt import REVIEW_PROMPT
from app.core.llm_factory import llm_factory

NUMERIC_FACT_PATTERN = re.compile(
    r"(?<![\w来源])\d+(?:\.\d+)?\s*(?:%|元|万元|秒|分钟|小时|天|个|条|倍)"
)
CITATION_PATTERN = re.compile(r"\[来源(\d+)]")


class ReviewEvaluator(Protocol):
    async def evaluate(
        self,
        user_query: str,
        candidate_answer: str,
        evidence: list[Evidence],
        errors: list[str],
        memory_context: str = "",
    ) -> ReviewResult: ...


class LLMReviewEvaluator:
    def __init__(self, model: BaseChatModel | None = None) -> None:
        self._model = model

    async def evaluate(
        self,
        user_query: str,
        candidate_answer: str,
        evidence: list[Evidence],
        errors: list[str],
        memory_context: str = "",
    ) -> ReviewResult:
        model = self._model or llm_factory.create_chat_model(role="reviewer")
        chain = REVIEW_PROMPT | model.with_structured_output(ReviewResult)
        response = await chain.ainvoke(
            {
                "user_query": user_query,
                "candidate_answer": candidate_answer,
                "evidence_summary": self._format_evidence(evidence),
                "errors": "；".join(errors) or "无",
                "memory_context": memory_context or "无",
            }
        )
        return (
            response
            if isinstance(response, ReviewResult)
            else ReviewResult.model_validate(response)
        )

    @staticmethod
    def _format_evidence(evidence: list[Evidence]) -> str:
        if not evidence:
            return "无"
        return "\n".join(
            f"[来源{index}] {item.source_type}/{item.source_name}: {item.content[:800]}"
            for index, item in enumerate(evidence, 1)
        )


class ReviewerAgent:
    """Reject obvious unsupported claims before optional semantic review."""

    name = "reviewer"

    def __init__(self, evaluator: ReviewEvaluator | None = None) -> None:
        self._evaluator = evaluator

    async def review(
        self,
        user_query: str,
        candidate_answer: str,
        evidence: list[Evidence],
        errors: list[str],
        memory_context: str = "",
    ) -> ReviewResult:
        deterministic = self._deterministic_review(
            candidate_answer, evidence, errors, memory_context
        )
        if deterministic is not None:
            return deterministic
        evaluator = self._evaluator or LLMReviewEvaluator()
        return await evaluator.evaluate(
            user_query, candidate_answer, evidence, errors, memory_context
        )

    @staticmethod
    def _deterministic_review(
        candidate_answer: str,
        evidence: list[Evidence],
        errors: list[str],
        memory_context: str = "",
    ) -> ReviewResult | None:
        issues: list[str] = []
        retry_target: RetryTarget = "supervisor"
        if NUMERIC_FACT_PATTERN.search(candidate_answer) and not evidence:
            issues.append("候选答案包含数字事实，但没有任何证据。")
            retry_target = "knowledge"
        knowledge_evidence = [item for item in evidence if item.source_type == "knowledge"]
        citations = [int(index) for index in CITATION_PATTERN.findall(candidate_answer)]
        if knowledge_evidence and (
            not citations or any(index < 1 or index > len(evidence) for index in citations)
        ):
            issues.append("知识结论缺少可映射到已有证据的有效引用。")
            retry_target = "knowledge"
        if errors and not any(marker in candidate_answer for marker in ("失败", "错误", "未完成")):
            issues.append("候选答案没有披露已记录的工具或 Agent 错误。")
            retry_target = "supervisor"
        current_markers = ("当前", "已恢复", "无告警", "正常")
        if "过期动态观察" in memory_context and any(
            marker in candidate_answer for marker in current_markers
        ):
            issues.append("候选答案将过期动态观察表述为当前状态，必须重新查询。")
            retry_target = "data"
        if not issues:
            return None
        return ReviewResult(
            passed=False,
            completeness_score=0.5,
            groundedness_score=0.0 if not evidence else 0.5,
            issues=issues,
            retry_target=retry_target,
            retry_instruction="修复列出的质量问题，并且只能使用已有或重新查询得到的证据。",
        )


reviewer_agent = ReviewerAgent()
