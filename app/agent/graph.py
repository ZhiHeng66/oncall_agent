"""Top-level bounded Supervisor-Specialist LangGraph."""

import asyncio
import re
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, Protocol
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
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
from app.agent.final_response import FinalResponseGenerator, final_response_generator
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
NODE_FINALIZE = "final_response"
CITATION_PATTERN = re.compile(r"\[来源(\d+)]")


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


class DataSpecialist(Protocol):
    async def run(
        self,
        task: AgentTask,
        *,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentResult: ...


class TaskSpecialist(Protocol):
    async def run(self, task: AgentTask, thread_id: str | None = None) -> AgentResult: ...

    def stream(
        self, task: AgentTask, thread_id: str | None = None
    ) -> AsyncGenerator[dict[str, Any], None]: ...


class MultiAgentGraph:
    """Route, dispatch, aggregate and review work with hard loop limits."""

    def __init__(
        self,
        supervisor: Supervisor = supervisor_agent,
        knowledge: Specialist = knowledge_agent,
        data: DataSpecialist = data_agent,
        task: TaskSpecialist = task_agent,
        reviewer: Reviewer = reviewer_agent,
        final_response: FinalResponseGenerator = final_response_generator,
        limits: RuntimeLimits = runtime_limits,
        checkpointers: CheckpointerFactory = checkpointer_factory,
    ) -> None:
        self._supervisor: Supervisor = supervisor
        self._knowledge = knowledge
        self._data = data
        self._task = task
        self._reviewer: Reviewer = reviewer
        self._final_response = final_response
        self._limits = limits
        self.checkpointer = checkpointers.create_memory()
        self.graph = self._build_graph()

    def set_checkpointer(self, checkpointer: BaseCheckpointSaver) -> None:
        """Recompile the graph with a lifespan-managed persistent saver."""

        self.checkpointer = checkpointer
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
        get_stream_writer()(
            {
                "type": "route_decided",
                "agent": "supervisor",
                "data": {
                    "mode": decision.mode,
                    "target_agents": decision.target_agents,
                    "reason_summary": decision.reason_summary,
                    "round": round_number,
                },
            }
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
        writer = get_stream_writer()
        return await self._dispatch(
            "data",
            state,
            lambda task: self._data.run(task, event_sink=writer),
        )

    async def task_node(self, state: MultiAgentState) -> dict[str, Any]:
        task = state["dispatch_task"]
        if task is None:
            raise ValueError("task dispatch task is missing")
        writer = get_stream_writer()
        writer(
            {
                "type": "agent_started",
                "agent": "task",
                "data": {"task_id": task.task_id, "round": state["supervisor_rounds"]},
            }
        )
        started = time.perf_counter()
        result: AgentResult | None = None
        try:
            async with asyncio.timeout(self._limits.agent_timeout_seconds):
                async for event in self._task.stream(task, thread_id=state["thread_id"]):
                    if event.get("type") in {"run_completed", "run_failed"}:
                        result = AgentResult.model_validate(event["result"])
                        continue
                    event_data = dict(event.get("data", {}))
                    event_data.update(
                        {
                            key: value
                            for key, value in event.items()
                            if key not in {"type", "agent", "data"}
                        }
                    )
                    writer(
                        {
                            "type": event.get("type", "step_completed"),
                            "agent": event.get("agent", "task"),
                            "data": event_data,
                        }
                    )
            if result is None:
                raise RuntimeError("Task Agent 流未返回终态结果")
        except Exception as exc:
            detail = sanitize_error_message(str(exc))
            result = AgentResult(
                agent_name="task",
                task_id=task.task_id,
                status="failed",
                summary="task Agent 执行失败。",
                errors=[detail],
            )
        return self._finish_dispatch("task", task, result, state, started, writer)

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
        writer = get_stream_writer()
        writer(
            {
                "type": "agent_started",
                "agent": agent_name,
                "data": {"task_id": task.task_id, "round": state["supervisor_rounds"]},
            }
        )
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
        return self._finish_dispatch(agent_name, task, result, state, started, writer)

    @staticmethod
    def _finish_dispatch(
        agent_name: AgentTarget,
        task: AgentTask,
        result: AgentResult,
        state: MultiAgentState,
        started: float,
        writer: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        duration_ms = (time.perf_counter() - started) * 1000
        record = DispatchRecord(
            round=state["supervisor_rounds"],
            agent_name=agent_name,
            task_id=task.task_id,
            status=result.status,
            duration_ms=duration_ms,
        )
        writer(
            {
                "type": "agent_completed",
                "agent": agent_name,
                "data": {
                    "task_id": task.task_id,
                    "round": state["supervisor_rounds"],
                    "status": result.status,
                    "duration_ms": duration_ms,
                },
            }
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
        evidence_indexes = self._evidence_indexes(state["agent_results"])
        for result in latest:
            summary = self._rewrite_citations(result.summary, result.evidence, evidence_indexes)
            sections.append(f"[{result.agent_name}:{result.status}]\n{summary}")
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
        writer = get_stream_writer()
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
            writer(self._review_event(review))
            return {
                "review_result": review,
                "review_history": [review],
                "workflow_errors": [detail],
                "status": "partial" if state["status"] != "failed" else "failed",
            }
        writer(self._review_event(review))
        return {"review_result": review, "review_history": [review]}

    @staticmethod
    def _review_event(review: ReviewResult) -> dict[str, Any]:
        return {
            "type": "review_completed",
            "agent": "reviewer",
            "data": {
                "passed": review.passed,
                "issues": review.issues,
                "retry_target": review.retry_target,
                "completeness_score": review.completeness_score,
                "groundedness_score": review.groundedness_score,
            },
        }

    @staticmethod
    def prepare_retry_node(state: MultiAgentState) -> dict[str, Any]:
        return {"retry_count": state["retry_count"] + 1}

    async def finalize_node(self, state: MultiAgentState) -> dict[str, Any]:
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

        answer = "\n".join(
            sanitize_error_message(line, max_length=max(len(line), 4))
            for line in answer.splitlines()
        )

        should_generate = status != "failed" and (review is None or review.passed)
        if should_generate:
            writer = get_stream_writer()
            try:
                answer = await self._final_response.generate(
                    state["user_query"],
                    answer,
                    event_sink=lambda content: writer(
                        {"type": "final_content_delta", "data": {"content": content}}
                    ),
                )
            except Exception as exc:
                detail = sanitize_error_message(str(exc))
                return {
                    "final_answer": answer,
                    "status": "partial",
                    "termination_reason": "finalization_failed",
                    "workflow_errors": [detail],
                }
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
                config=self._config(thread_id, resolved_run_id),
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

    async def stream(
        self,
        user_query: str,
        *,
        user_id: str,
        thread_id: str,
        run_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        resolved_run_id = run_id or str(uuid4())
        graph_config = self._config(thread_id, resolved_run_id)
        try:
            async for mode, event in self.graph.astream(
                self._initial_state(user_query, user_id, thread_id, resolved_run_id),
                config=graph_config,
                stream_mode=["custom", "updates"],
            ):
                if mode == "custom":
                    yield event
            snapshot = await self.graph.aget_state(graph_config)
            result = self._to_result(snapshot.values)
        except Exception as exc:
            detail = sanitize_error_message(str(exc))
            result = MultiAgentRunResult(
                run_id=resolved_run_id,
                thread_id=thread_id,
                status="failed",
                final_answer="Multi-Agent 图执行失败。",
                errors=[detail],
                termination_reason="graph_failed",
            )
        yield {"type": "graph_completed", "result": result.model_dump(mode="json")}

    async def delete_run_state(self, thread_id: str, run_id: str) -> None:
        await self.checkpointer.adelete_thread(f"{thread_id}:{run_id}")

    def _config(self, thread_id: str, run_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": f"{thread_id}:{run_id}"},
            "recursion_limit": self._limits.graph_recursion_limit,
        }

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
    def _evidence_indexes(results: list[AgentResult]) -> dict[tuple[str, str | None], int]:
        indexes: dict[tuple[str, str | None], int] = {}
        for result in results:
            for evidence in result.evidence:
                key = (evidence.source_name, evidence.source_id)
                if key not in indexes:
                    indexes[key] = len(indexes) + 1
        return indexes

    @staticmethod
    def _rewrite_citations(
        summary: str,
        evidence: list[Evidence],
        evidence_indexes: dict[tuple[str, str | None], int],
    ) -> str:
        local_to_global = {
            local_index: evidence_indexes[(item.source_name, item.source_id)]
            for local_index, item in enumerate(evidence, 1)
        }
        return CITATION_PATTERN.sub(
            lambda match: f"[来源{local_to_global.get(int(match.group(1)), match.group(1))}]",
            summary,
        )

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
