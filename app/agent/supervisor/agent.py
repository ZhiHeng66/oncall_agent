"""Bounded Supervisor Agent with structured routing and deterministic fallback."""

from typing import Protocol, cast

from langchain_core.language_models.chat_models import BaseChatModel

from app.agent.contracts import AgentResult, AgentTarget, AgentTask, ReviewResult, RouteDecision
from app.agent.supervisor.prompt import SUPERVISOR_ROUTE_PROMPT
from app.core.llm_factory import llm_factory


class SupervisorRouter(Protocol):
    async def decide(
        self,
        user_query: str,
        previous_results: list[AgentResult],
        review: ReviewResult | None,
        round_number: int,
    ) -> RouteDecision: ...


class LLMSupervisorRouter:
    def __init__(self, model: BaseChatModel | None = None) -> None:
        self._model = model

    async def decide(
        self,
        user_query: str,
        previous_results: list[AgentResult],
        review: ReviewResult | None,
        round_number: int,
    ) -> RouteDecision:
        model = self._model or llm_factory.create_chat_model(role="supervisor")
        chain = SUPERVISOR_ROUTE_PROMPT | model.with_structured_output(RouteDecision)
        response = await chain.ainvoke(
            {
                "user_query": user_query,
                "previous_results": self._format_results(previous_results),
                "review_feedback": self._format_review(review),
                "round_number": round_number,
            }
        )
        return (
            response
            if isinstance(response, RouteDecision)
            else RouteDecision.model_validate(response)
        )

    @staticmethod
    def _format_results(results: list[AgentResult]) -> str:
        if not results:
            return "无"
        return "\n".join(
            f"- {item.agent_name}/{item.task_id} [{item.status}]: {item.summary[:500]}"
            for item in results
        )

    @staticmethod
    def _format_review(review: ReviewResult | None) -> str:
        if review is None:
            return "无"
        return f"问题：{'；'.join(review.issues)}；返工要求：{review.retry_instruction or '无'}"


class SupervisorAgent:
    """Choose only known execution units and convert review feedback into bounded work."""

    name = "supervisor"

    def __init__(self, router: SupervisorRouter | None = None) -> None:
        self._router = router

    async def route(
        self,
        user_query: str,
        previous_results: list[AgentResult],
        review: ReviewResult | None,
        round_number: int,
    ) -> RouteDecision:
        if review and review.retry_target in {"knowledge", "data", "task"}:
            return self._directed_retry(user_query, review, round_number)
        try:
            router = self._router or LLMSupervisorRouter()
            return await router.decide(user_query, previous_results, review, round_number)
        except Exception:
            return self._fallback_route(user_query, round_number)

    @staticmethod
    def _directed_retry(
        user_query: str,
        review: ReviewResult,
        round_number: int,
    ) -> RouteDecision:
        target = cast(AgentTarget, review.retry_target)
        assert target in {"knowledge", "data", "task"}
        task = AgentTask(
            task_id=f"retry-{round_number}-{target}",
            objective=review.retry_instruction or user_query,
            context=f"原始请求：{user_query}\n审核问题：{'；'.join(review.issues)}",
            constraints=["只修复审核指出的问题，不得编造新证据"],
        )
        return RouteDecision(
            mode="complex" if target == "task" else "single",
            target_agents=[target],
            tasks=[task],
            reason_summary=f"根据审核反馈定向返工至 {target} Agent。",
        )

    @staticmethod
    def _fallback_route(user_query: str, round_number: int) -> RouteDecision:
        lowered = user_query.lower()
        knowledge_words = ("文档", "制度", "规范", "知识库", "手册", "document", "policy")
        data_words = ("实时", "当前", "监控", "指标", "外部数据", "time", "metric")
        complex_words = ("分步骤", "制定方案", "先", "再", "综合分析", "执行计划")
        needs_knowledge = any(word in lowered for word in knowledge_words)
        needs_data = any(word in lowered for word in data_words)
        task_id = f"route-{round_number}"
        if any(word in lowered for word in complex_words):
            return RouteDecision(
                mode="complex",
                target_agents=["task"],
                tasks=[AgentTask(task_id=task_id, objective=user_query)],
                reason_summary="结构化路由不可用；确定性规则识别到有依赖的多步骤目标。",
            )
        if needs_knowledge and needs_data:
            return RouteDecision(
                mode="parallel",
                target_agents=["knowledge", "data"],
                tasks=[
                    AgentTask(task_id=f"{task_id}-knowledge", objective=user_query),
                    AgentTask(task_id=f"{task_id}-data", objective=user_query),
                ],
                reason_summary="结构化路由不可用；确定性规则识别到内部知识和外部数据需求。",
            )
        if needs_knowledge or needs_data:
            target: AgentTarget = "knowledge" if needs_knowledge else "data"
            return RouteDecision(
                mode="single",
                target_agents=[target],
                tasks=[AgentTask(task_id=task_id, objective=user_query)],
                reason_summary=f"结构化路由不可用；确定性规则选择 {target} Agent。",
            )
        return RouteDecision(
            mode="direct",
            reason_summary="结构化路由不可用；确定性规则未识别到外部能力需求。",
            direct_answer="当前无法生成直接回答，请稍后重试。",
        )


supervisor_agent = SupervisorAgent()
