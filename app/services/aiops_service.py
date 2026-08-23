"""Compatibility service adapting the generic Task Agent to legacy AIOps events."""

from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from app.agent.contracts import AgentTask
from app.agent.specialists.task.graph import TaskAgent, task_agent


class AIOpsService:
    """Expose the Task Agent through the existing execute/diagnose service contract."""

    def __init__(self, task_runner: TaskAgent = task_agent) -> None:
        self.task_agent = task_runner

    async def execute(
        self,
        user_input: str,
        session_id: str = "default",
    ) -> AsyncGenerator[dict[str, Any], None]:
        task = AgentTask(
            task_id=f"task-{uuid4()}",
            objective=user_input,
            expected_output="基于实际证据完成任务，并披露失败和未完成项。",
        )
        async for event in self.task_agent.stream(task, thread_id=session_id):
            event_type = event["type"]
            if event_type == "plan_created":
                plan = event["plan"]
                yield {
                    "type": "plan",
                    "stage": "plan_created",
                    "message": f"执行计划已制定，共 {len(plan)} 个步骤",
                    "plan": plan,
                }
            elif event_type == "step_started":
                yield {
                    "type": "status",
                    "stage": "step_started",
                    "message": f"开始执行步骤 {event['step_index']}",
                    "current_step": event["step"],
                }
            elif event_type in {"step_completed", "step_failed"}:
                yield {
                    "type": "step_complete",
                    "stage": "step_executed",
                    "message": "步骤执行完成" if event_type == "step_completed" else "步骤执行失败",
                    "current_step": event["step"],
                    "result_preview": event["summary"][:300],
                    "status": event["status"],
                }
            elif event_type in {"run_completed", "run_failed"}:
                result = event["result"]
                if result["summary"]:
                    yield {
                        "type": "report",
                        "stage": "final_report",
                        "message": "最终结果已生成",
                        "report": result["summary"],
                        "evidence": result["evidence"],
                    }
                terminal_type = "complete" if result["status"] != "failed" else "error"
                yield {
                    "type": terminal_type,
                    "stage": "complete" if terminal_type == "complete" else "error",
                    "message": "任务执行完成" if terminal_type == "complete" else "任务执行失败",
                    "response": result["summary"],
                    "status": result["status"],
                    "errors": result["errors"],
                }

    async def diagnose(self, session_id: str = "default") -> AsyncGenerator[dict[str, Any], None]:
        """Keep the legacy diagnosis entrypoint as a fixed Task Agent adapter."""
        objective = (
            "查询当前系统活动告警，结合可用的内部处理资料和只读外部数据分析原因，"
            "形成包含证据、失败披露和处理建议的 Markdown 诊断报告。"
        )
        async for event in self.execute(objective, session_id):
            if event.get("type") == "complete":
                yield {
                    "type": "complete",
                    "stage": "diagnosis_complete",
                    "message": "诊断流程完成",
                    "diagnosis": {
                        "status": event.get("status", "completed"),
                        "report": event.get("response", ""),
                    },
                }
            else:
                yield event


aiops_service = AIOpsService()
