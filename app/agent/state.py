"""Top-level state for Supervisor-Specialist orchestration."""

import operator
from typing import Annotated, TypedDict

from app.agent.contracts import (
    AgentResult,
    AgentTask,
    DispatchRecord,
    ReviewResult,
    RouteDecision,
    RunStatus,
)
from app.agent.memory.contracts import MemoryContextView, MemoryUsage, WorkingMemoryContext


class MultiAgentState(TypedDict):
    user_id: str
    thread_id: str
    run_id: str
    user_query: str
    working_memory: WorkingMemoryContext | None
    memory_view: MemoryContextView
    memory_usage: MemoryUsage
    route_decision: RouteDecision | None
    pending_tasks: list[AgentTask]
    dispatch_task: AgentTask | None
    agent_results: Annotated[list[AgentResult], operator.add]
    dispatch_records: Annotated[list[DispatchRecord], operator.add]
    candidate_answer: str
    review_result: ReviewResult | None
    review_history: Annotated[list[ReviewResult], operator.add]
    retry_count: int
    supervisor_rounds: int
    final_answer: str
    status: RunStatus
    termination_reason: str
    workflow_errors: Annotated[list[str], operator.add]
