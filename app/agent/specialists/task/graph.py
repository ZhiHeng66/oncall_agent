"""LangGraph Plan-Execute-Replan subgraph implementing the Task Agent."""

from collections.abc import AsyncGenerator
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.agent.contracts import AgentResult, AgentTask, TaskStepResult
from app.agent.mcp_client import sanitize_error_message
from app.agent.runtime.checkpointer import CheckpointerFactory, checkpointer_factory
from app.agent.runtime.limits import RuntimeLimits, runtime_limits
from app.agent.specialists.task.executor import (
    SpecialistTaskStepExecutor,
    TaskStepExecutor,
)
from app.agent.specialists.task.planner import LLMTaskPlanner, TaskPlanner
from app.agent.specialists.task.replanner import LLMTaskReplanner, TaskReplanner
from app.agent.specialists.task.state import TaskState

NODE_PLANNER = "planner"
NODE_EXECUTOR = "executor"
NODE_REPLANNER = "replanner"


class TaskAgent:
    """Execute dependent multi-step work inside a bounded LangGraph subgraph."""

    name = "task"

    def __init__(
        self,
        planner: TaskPlanner | None = None,
        executor: TaskStepExecutor | None = None,
        replanner: TaskReplanner | None = None,
        limits: RuntimeLimits = runtime_limits,
        checkpointers: CheckpointerFactory = checkpointer_factory,
    ) -> None:
        self._planner = planner or LLMTaskPlanner(limits=limits)
        self._executor = executor or SpecialistTaskStepExecutor()
        self._replanner = replanner or LLMTaskReplanner()
        self._limits = limits
        self.checkpointer = checkpointers.create_memory()
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(TaskState)
        workflow.add_node(NODE_PLANNER, self.plan_node)
        workflow.add_node(NODE_EXECUTOR, self.execute_node)
        workflow.add_node(NODE_REPLANNER, self.replan_node)
        workflow.add_edge(START, NODE_PLANNER)
        workflow.add_conditional_edges(
            NODE_PLANNER,
            self._next_after_plan,
            {NODE_EXECUTOR: NODE_EXECUTOR, END: END},
        )
        workflow.add_edge(NODE_EXECUTOR, NODE_REPLANNER)
        workflow.add_conditional_edges(
            NODE_REPLANNER,
            self._next_after_replan,
            {NODE_EXECUTOR: NODE_EXECUTOR, END: END},
        )
        return workflow.compile(checkpointer=self.checkpointer)

    async def plan_node(self, state: TaskState) -> dict[str, Any]:
        proposed_steps = await self._planner.plan(state["task"])
        steps = proposed_steps[: self._limits.task_max_steps]
        if not steps:
            return {
                "final_answer": "无法为该任务生成可执行计划。",
                "status": "failed",
                "termination_reason": "empty_plan",
            }
        return {
            "plan": steps,
            "plan_was_truncated": len(proposed_steps) > self._limits.task_max_steps,
        }

    async def execute_node(self, state: TaskState) -> dict[str, Any]:
        if not state["plan"]:
            return {}
        step = state["plan"][0]
        writer = get_stream_writer()
        writer({"type": "step_started", "step": step, "step_index": state["step_count"] + 1})
        try:
            result = await self._executor.execute(state["task"], step, state["past_steps"])
        except Exception as exc:
            detail = sanitize_error_message(str(exc))
            result = TaskStepResult(
                step=step,
                status="failed",
                summary=f"步骤执行失败: {detail}",
                delegated_agent="unknown",
                errors=[detail],
            )
        writer(
            {
                "type": "step_completed" if result.status != "failed" else "step_failed",
                "step": step,
                "step_index": state["step_count"] + 1,
                "status": result.status,
                "summary": result.summary,
            }
        )
        return {
            "plan": state["plan"][1:],
            "past_steps": [result],
            "step_count": state["step_count"] + 1,
        }

    async def replan_node(self, state: TaskState) -> dict[str, Any]:
        remaining_budget = self._limits.task_max_steps - state["step_count"]
        if remaining_budget <= 0:
            return self._finalize_state(state, "max_steps")

        try:
            decision = await self._replanner.decide(
                state["task"],
                state["plan"],
                state["past_steps"],
                remaining_budget,
            )
        except Exception as exc:
            detail = sanitize_error_message(str(exc))
            update = self._finalize_state(state, "replanner_failed")
            update["workflow_errors"] = [detail]
            update["status"] = "partial" if state["past_steps"] else "failed"
            update["final_answer"] += f"\n\n重新规划失败：{detail}"
            return update
        if decision.action == "respond":
            return self._finalize_state(state, "responded", decision.response)
        if decision.action == "replan":
            new_steps = decision.new_steps[:remaining_budget]
            if not new_steps:
                return self._finalize_state(state, "empty_replan", decision.response)
            return {"plan": new_steps}
        if not state["plan"]:
            return self._finalize_state(state, "plan_completed", decision.response)
        return {}

    @staticmethod
    def _next_after_plan(state: TaskState) -> str:
        return END if state["final_answer"] else NODE_EXECUTOR

    @staticmethod
    def _next_after_replan(state: TaskState) -> str:
        return END if state["final_answer"] else NODE_EXECUTOR

    def _finalize_state(
        self,
        state: TaskState,
        reason: str,
        response: str = "",
    ) -> dict[str, Any]:
        failed = [item for item in state["past_steps"] if item.status == "failed"]
        successful = [item for item in state["past_steps"] if item.status != "failed"]
        if failed and successful:
            status = "partial"
        elif failed:
            status = "failed"
        elif reason == "max_steps" and (state["plan"] or state["plan_was_truncated"]):
            status = "partial"
        else:
            status = "success"
        answer = response.strip() or self._format_step_summary(state["past_steps"])
        if reason == "max_steps" and (state["plan"] or state["plan_was_truncated"]):
            answer += "\n\n已达到最大步骤数，仍有未执行步骤。"
        return {
            "final_answer": answer,
            "status": status,
            "termination_reason": reason,
        }

    async def run(
        self, task: AgentTask, thread_id: str | None = None, run_id: str | None = None
    ) -> AgentResult:
        try:
            result = await self.graph.ainvoke(
                self._initial_state(task),
                config=self._config(task, thread_id, run_id),
            )
        except Exception as exc:
            detail = sanitize_error_message(str(exc))
            return AgentResult(
                agent_name=self.name,
                task_id=task.task_id,
                status="failed",
                summary="复杂任务执行失败。",
                errors=[detail],
            )
        return self._to_result(result)

    async def stream(
        self,
        task: AgentTask,
        thread_id: str | None = None,
        run_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        try:
            async for mode, event in self.graph.astream(
                self._initial_state(task),
                config=self._config(task, thread_id, run_id),
                stream_mode=["custom", "updates"],
            ):
                if mode == "custom":
                    yield event
                elif NODE_PLANNER in event and event[NODE_PLANNER].get("plan"):
                    yield {"type": "plan_created", "plan": event[NODE_PLANNER]["plan"]}

            snapshot = self.graph.get_state(self._config(task, thread_id, run_id))
            result = self._to_result(snapshot.values)
            terminal_type = "run_completed" if result.status != "failed" else "run_failed"
            yield {"type": terminal_type, "result": result.model_dump(mode="json")}
        except Exception as exc:
            detail = sanitize_error_message(str(exc))
            yield {
                "type": "run_failed",
                "result": AgentResult(
                    agent_name=self.name,
                    task_id=task.task_id,
                    status="failed",
                    summary="复杂任务执行失败。",
                    errors=[detail],
                ).model_dump(mode="json"),
            }

    @staticmethod
    def _initial_state(task: AgentTask) -> TaskState:
        return {
            "task": task,
            "plan": [],
            "past_steps": [],
            "step_count": 0,
            "plan_was_truncated": False,
            "final_answer": "",
            "status": "success",
            "termination_reason": "",
            "workflow_errors": [],
        }

    def _config(
        self, task: AgentTask, thread_id: str | None, run_id: str | None = None
    ) -> dict[str, Any]:
        internal_thread = f"{thread_id or task.task_id}:{run_id or task.task_id}:{task.task_id}"
        return {
            "configurable": {"thread_id": internal_thread},
            "recursion_limit": self._limits.graph_recursion_limit,
        }

    def _to_result(self, state: TaskState | dict[str, Any]) -> AgentResult:
        steps = [TaskStepResult.model_validate(item) for item in state.get("past_steps", [])]
        task = AgentTask.model_validate(state["task"])
        evidence = [item for step in steps for item in step.evidence]
        errors = [
            *[item for step in steps for item in step.errors],
            *state.get("workflow_errors", []),
        ]
        return AgentResult(
            agent_name=self.name,
            task_id=task.task_id,
            status=state.get("status", "failed"),
            summary=state.get("final_answer", "") or self._format_step_summary(steps),
            evidence=evidence,
            artifacts=[
                {
                    "steps": [step.model_dump(mode="json") for step in steps],
                    "termination_reason": state.get("termination_reason", ""),
                }
            ],
            errors=errors,
        )

    @staticmethod
    def _format_step_summary(steps: list[TaskStepResult]) -> str:
        return (
            "\n".join(
                f"{index}. {item.step} [{item.status}]\n{item.summary}"
                for index, item in enumerate(steps, 1)
            )
            or "没有完成任何任务步骤。"
        )


task_agent = TaskAgent()
