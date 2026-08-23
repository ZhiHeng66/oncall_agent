"""Top-level bounded Supervisor-Specialist LangGraph."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.agent.contracts import (
    AgentResult,
    AgentTarget,
    AgentTask,
    DispatchRecord,
    Evidence,
    MultiAgentRunResult,
    ReviewResult,
    RouteDecision,
)
from app.agent.mcp_client import sanitize_error_message
from app.agent.runtime.checkpointer import CheckpointerFactory, checkpointer_factory
from app.agent.runtime.limits import RuntimeLimits, runtime_limits
from app.agent.specialists.data.agent import data_agent
from app.agent.specialists.knowledge.agent import knowledge_agent
from app.agent.specialists.reviewer.agent import reviewer_agent
from app.agent.specialists.task.graph import task_agent
from app.agent.state import MultiAgentState
from app.agent.supervisor.agent import supervisor_agent

NODE_SUPERVISOR = "supervisor"
NODE_KNOWLEDGE = "knowledge_agent"
NODE_DATA = "data_agent"
NODE_TASK = "task_agent"
NODE_AGGREGATE = "aggregate"
NODE_REVIEWER = "reviewer"
NODE_PREPARE_RETRY = "prepare_retry"
NODE_FINALIZE = "finalize"


class Supervisor(Protocol):
    async def route(
        self,
        user_query: str,
        previous_results: list[AgentResult],
        review: ReviewResult | None,
        round_number: int,
    ) -> RouteDecision: ...


class Reviewer(Protocol):
    async def review(
        self,
        user_query: str,
        candidate_answer: str,
        evidence: list[Evidence],
        errors: list[str],
    ) -> ReviewResult: ...


class Specialist(Protocol):
    async def run(self, task: AgentTask) -> AgentResult: ...


class TaskSpecialist(Protocol):
    async def run(self, task: AgentTask, thread_id: str | None = None) -> AgentResult: ...


class MultiAgentGraph:
    """Route, dispatch, aggregate and review work with hard loop limits."""

    def __init__(
        self,
        supervisor: Supervisor = supervisor_agent,
        knowledge: Specialist = knowledge_agent,
        data: Specialist = data_agent,
        task: TaskSpecialist = task_agent,
        reviewer: Reviewer = reviewer_agent,
        limits: RuntimeLimits = runtime_limits,
        checkpointers: CheckpointerFactory = checkpointer_factory,
    ) -> None:
        self._supervisor: Supervisor = supervisor
        self._knowledge = knowledge
        self._data = data
        self._task = task
        self._reviewer: Reviewer = reviewer
        self._limits = limits
        self.checkpointer = checkpointers.create()
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(MultiAgentState)
        workflow.add_node(NODE_SUPERVISOR, self.supervisor_node)
        workflow.add_node(NODE_KNOWLEDGE, self.knowledge_node)
        workflow.add_node(NODE_DATA, self.data_node)
        workflow.add_node(NODE_TASK, self.task_node)
        workflow.add_node(NODE_AGGREGATE, self.aggregate_node)
        workflow.add_node(NODE_REVIEWER, self.reviewer_node)
        workflow.add_node(NODE_PREPARE_RETRY, self.prepare_retry_node)
        workflow.add_node(NODE_FINALIZE, self.finalize_node)
        workflow.add_edge(START, NODE_SUPERVISOR)
        workflow.add_conditional_edges(NODE_SUPERVISOR, self.route_after_supervisor)
        workflow.add_edge(NODE_KNOWLEDGE, NODE_AGGREGATE)
        workflow.add_edge(NODE_DATA, NODE_AGGREGATE)
        workflow.add_edge(NODE_TASK, NODE_AGGREGATE)
        workflow.add_conditional_edges(
            NODE_AGGREGATE,
            self.route_after_aggregate,
            {NODE_REVIEWER: NODE_REVIEWER, NODE_FINALIZE: NODE_FINALIZE},
        )
        workflow.add_conditional_edges(
            NODE_REVIEWER,
            self.route_after_review,
            {NODE_PREPARE_RETRY: NODE_PREPARE_RETRY, NODE_FINALIZE: NODE_FINALIZE},
        )
        workflow.add_edge(NODE_PREPARE_RETRY, NODE_SUPERVISOR)
        workflow.add_edge(NODE_FINALIZE, END)
        return workflow.compile(checkpointer=self.checkpointer)

    async def supervisor_node(self, state: MultiAgentState) -> dict[str, Any]:
        if state["supervisor_rounds"] >= self._limits.supervisor_max_rounds:
            answer = state["candidate_answer"] or "Supervisor 已达到最大调度轮次。"
            return {
                "final_answer": f"{answer}\n\n已达到最大调度轮次，停止继续委派。",
                "status": "partial",
                "termination_reason": "supervisor_limit",
            }
        round_number = state["supervisor_rounds"] + 1
        decision = await self._supervisor.route(
            state["user_query"],
            state["agent_results"],
            state["review_result"],
            round_number,
        )
        return {
            "route_decision": decision,
            "pending_tasks": decision.tasks,
            "supervisor_rounds": round_number,
        }

    def route_after_supervisor(self, state: MultiAgentState) -> str | list[Send]:
        if state["final_answer"]:
            return NODE_FINALIZE
        decision = state["route_decision"]
        if decision is None or decision.mode == "direct":
            return NODE_AGGREGATE
        node_by_agent = {
            "knowledge": NODE_KNOWLEDGE,
            "data": NODE_DATA,
            "task": NODE_TASK,
        }
        return [
            Send(
                node_by_agent[target],
                {
                    **state,
                    "dispatch_task": task,
                },
            )
            for target, task in zip(decision.target_agents, decision.tasks, strict=True)
        ]

    async def knowledge_node(self, state: MultiAgentState) -> dict[str, Any]:
        return await self._dispatch(
            "knowledge",
            state,
            lambda task: self._knowledge.run(task),
        )

    async def data_node(self, state: MultiAgentState) -> dict[str, Any]:
        return await self._dispatch("data", state, lambda task: self._data.run(task))

    async def task_node(self, state: MultiAgentState) -> dict[str, Any]:
        return await self._dispatch(
            "task",
            state,
            lambda task: self._task.run(task, thread_id=state["thread_id"]),
        )

    async def _dispatch(
        self,
        agent_name: AgentTarget,
        state: MultiAgentState,
        invoke: Callable[[AgentTask], Awaitable[AgentResult]],
    ) -> dict[str, Any]:
        task = state["dispatch_task"]
        if task is None:
            raise ValueError(f"{agent_name} dispatch task is missing")
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._limits.agent_timeout_seconds):
                result = await invoke(task)
        except Exception as exc:
            detail = sanitize_error_message(str(exc))
            result = AgentResult(
                agent_name=agent_name,
                task_id=task.task_id,
                status="failed",
                summary=f"{agent_name} Agent 执行失败。",
                errors=[detail],
            )
        duration_ms = (time.perf_counter() - started) * 1000
        record = DispatchRecord(
            round=state["supervisor_rounds"],
            agent_name=agent_name,
            task_id=task.task_id,
            status=result.status,
            duration_ms=duration_ms,
        )
        return {"agent_results": [result], "dispatch_records": [record]}

    def aggregate_node(self, state: MultiAgentState) -> dict[str, Any]:
        decision = state["route_decision"]
        if decision and decision.mode == "direct":
            answer = decision.direct_answer.strip() or "Supervisor 未生成直接回答。"
            is_fallback_failure = answer.startswith("当前无法生成直接回答")
            return {
                "candidate_answer": answer,
                "status": "partial" if is_fallback_failure else "completed",
            }

        latest = self._latest_results(state["agent_results"])
        if not latest:
            return {
                "candidate_answer": "没有 Agent 返回可用结果。",
                "status": "failed",
            }
        sections: list[str] = []
        for result in latest:
            sections.append(f"[{result.agent_name}:{result.status}]\n{result.summary}")
            if result.errors:
                sections.append("未完成或错误：" + "；".join(result.errors))
        statuses = {item.status for item in latest}
        if statuses == {"failed"}:
            status = "failed"
        elif "failed" in statuses or "partial" in statuses:
            status = "partial"
        else:
            status = "completed"
        return {"candidate_answer": "\n\n".join(sections), "status": status}

    async def reviewer_node(self, state: MultiAgentState) -> dict[str, Any]:
        evidence = self._collect_evidence(state["agent_results"])
        errors = self._collect_errors(state["agent_results"], state["workflow_errors"])
        try:
            review = await self._reviewer.review(
                state["user_query"],
                state["candidate_answer"],
                evidence,
                errors,
            )
        except Exception as exc:
            detail = sanitize_error_message(str(exc))
            review = ReviewResult(
                passed=False,
                completeness_score=0.0,
                groundedness_score=0.0,
                issues=["Reviewer 执行失败，无法确认候选答案质量。"],
                retry_target="none",
            )
            return {
                "review_result": review,
                "review_history": [review],
                "workflow_errors": [detail],
                "status": "partial" if state["status"] != "failed" else "failed",
            }
        return {"review_result": review, "review_history": [review]}

    @staticmethod
    def prepare_retry_node(state: MultiAgentState) -> dict[str, Any]:
        return {"retry_count": state["retry_count"] + 1}

    def finalize_node(self, state: MultiAgentState) -> dict[str, Any]:
        if state["final_answer"]:
            return {}
        answer = state["candidate_answer"] or "没有生成最终答案。"
        status = state["status"]
        review = state["review_result"]
        if review and not review.passed:
            answer += "\n\n质量检查未通过：" + "；".join(review.issues)
            if status != "failed":
                status = "partial"
            reason = "review_limit" if state["retry_count"] else "review_rejected"
        elif review and review.passed:
            reason = "review_passed"
        else:
            reason = "fast_path"
        if status == "failed":
            reason = "all_specialists_failed"
        return {"final_answer": answer, "status": status, "termination_reason": reason}

    def route_after_aggregate(self, state: MultiAgentState) -> str:
        decision = state["route_decision"]
        is_fast_success = (
            state["retry_count"] == 0
            and state["status"] == "completed"
            and decision is not None
            and decision.mode in {"direct", "single"}
        )
        return NODE_FINALIZE if is_fast_success else NODE_REVIEWER

    def route_after_review(self, state: MultiAgentState) -> str:
        review = state["review_result"]
        can_retry = (
            review is not None
            and not review.passed
            and review.retry_target != "none"
            and state["retry_count"] < self._limits.reviewer_max_retries
            and state["supervisor_rounds"] < self._limits.supervisor_max_rounds
        )
        return NODE_PREPARE_RETRY if can_retry else NODE_FINALIZE

    async def run(
        self,
        user_query: str,
        *,
        user_id: str,
        thread_id: str,
        run_id: str | None = None,
    ) -> MultiAgentRunResult:
        resolved_run_id = run_id or str(uuid4())
        try:
            result = await self.graph.ainvoke(
                self._initial_state(user_query, user_id, thread_id, resolved_run_id),
                config={
                    "configurable": {"thread_id": f"{thread_id}:{resolved_run_id}"},
                    "recursion_limit": self._limits.graph_recursion_limit,
                },
            )
        except Exception as exc:
            detail = sanitize_error_message(str(exc))
            return MultiAgentRunResult(
                run_id=resolved_run_id,
                thread_id=thread_id,
                status="failed",
                final_answer="Multi-Agent 图执行失败。",
                errors=[detail],
                termination_reason="graph_failed",
            )
        return self._to_result(result)

    @staticmethod
    def _initial_state(
        user_query: str,
        user_id: str,
        thread_id: str,
        run_id: str,
    ) -> MultiAgentState:
        return {
            "user_id": user_id,
            "thread_id": thread_id,
            "run_id": run_id,
            "user_query": user_query,
            "route_decision": None,
            "pending_tasks": [],
            "dispatch_task": None,
            "agent_results": [],
            "dispatch_records": [],
            "candidate_answer": "",
            "review_result": None,
            "review_history": [],
            "retry_count": 0,
            "supervisor_rounds": 0,
            "final_answer": "",
            "status": "running",
            "termination_reason": "",
            "workflow_errors": [],
        }

    @staticmethod
    def _latest_results(results: list[AgentResult]) -> list[AgentResult]:
        latest: dict[str, AgentResult] = {}
        for result in results:
            latest[result.task_id] = result
        return list(latest.values())

    @staticmethod
    def _collect_evidence(results: list[AgentResult]) -> list[Evidence]:
        unique: dict[tuple[str, str | None, str], Evidence] = {}
        for result in results:
            for evidence in result.evidence:
                key = (evidence.source_name, evidence.source_id, evidence.content)
                unique[key] = evidence
        return list(unique.values())

    @staticmethod
    def _collect_errors(results: list[AgentResult], workflow_errors: list[str]) -> list[str]:
        return list(
            dict.fromkeys([*(error for item in results for error in item.errors), *workflow_errors])
        )

    def _to_result(self, state: MultiAgentState | dict[str, Any]) -> MultiAgentRunResult:
        agent_results = [AgentResult.model_validate(item) for item in state["agent_results"]]
        workflow_errors = list(state.get("workflow_errors", []))
        return MultiAgentRunResult(
            run_id=state["run_id"],
            thread_id=state["thread_id"],
            status=state["status"],
            final_answer=state["final_answer"],
            route=state.get("route_decision"),
            agent_results=agent_results,
            review_history=[ReviewResult.model_validate(item) for item in state["review_history"]],
            dispatch_records=[
                DispatchRecord.model_validate(item) for item in state["dispatch_records"]
            ],
            errors=self._collect_errors(agent_results, workflow_errors),
            termination_reason=state["termination_reason"],
        )


multi_agent_graph = MultiAgentGraph()
