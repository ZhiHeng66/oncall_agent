"""Internal state for the Plan-Execute-Replan Task Agent subgraph."""

import operator
from typing import Annotated, TypedDict

from app.agent.contracts import AgentStatus, AgentTask, TaskStepResult


class TaskState(TypedDict):
    task: AgentTask
    plan: list[str]
    past_steps: Annotated[list[TaskStepResult], operator.add]
    step_count: int
    plan_was_truncated: bool
    final_answer: str
    status: AgentStatus
    termination_reason: str
    workflow_errors: Annotated[list[str], operator.add]
