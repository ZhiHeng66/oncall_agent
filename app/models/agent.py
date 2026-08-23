"""Public request, response and event contracts for the unified Agent API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent.contracts import AgentTarget, RouteMode, RunStatus

AgentEventType = Literal[
    "run_started",
    "route_decided",
    "agent_started",
    "agent_completed",
    "plan_created",
    "step_started",
    "tool_started",
    "tool_completed",
    "step_completed",
    "step_failed",
    "review_completed",
    "content_delta",
    "run_completed",
    "run_failed",
]
PublicAgentName = Literal["supervisor", "knowledge", "data", "task", "reviewer"]


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "user-001",
                "thread_id": "thread-001",
                "message": "结合内部产品手册和当前数据分析问题原因",
            }
        }
    )

    user_id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("user_id", "thread_id", "message")
    @classmethod
    def reject_blank_values(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value cannot be blank")
        return cleaned


class AgentSource(BaseModel):
    source_type: Literal["knowledge", "mcp", "tool"]
    source_name: str
    source_id: str | None = None
    excerpt: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRouteSummary(BaseModel):
    mode: RouteMode
    target_agents: list[AgentTarget] = Field(default_factory=list)
    reason_summary: str


class AgentRunResponse(BaseModel):
    run_id: str
    thread_id: str
    status: RunStatus
    answer: str
    sources: list[AgentSource] = Field(default_factory=list)
    agents: list[PublicAgentName] = Field(default_factory=list)
    route: AgentRouteSummary | None = None
    errors: list[str] = Field(default_factory=list)
    termination_reason: str


class AgentEventEnvelope(BaseModel):
    event_id: str
    run_id: str
    thread_id: str
    type: AgentEventType
    timestamp: datetime
    agent: PublicAgentName | None = None
    node: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class AgentThreadResponse(BaseModel):
    thread_id: str
    run_ids: list[str] = Field(default_factory=list)
    history: list[dict[str, str]] = Field(default_factory=list)


class AgentThreadDeleteResponse(BaseModel):
    thread_id: str
    deleted_runs: int


class AgentRunMetrics(BaseModel):
    duration_ms: float
    dispatch_count: int = 0
    tool_call_count: int = 0
    task_step_count: int = 0
    failed: bool = False
    timed_out: bool = False
    model_calls: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    token_cost: float | None = None


class AgentRunTraceResponse(BaseModel):
    run: AgentRunResponse
    events: list[AgentEventEnvelope] = Field(default_factory=list)
    metrics: AgentRunMetrics


class AgentMetricsResponse(BaseModel):
    total_runs: int
    failure_rate: float
    timeout_rate: float
    average_duration_ms: float
    average_dispatch_count: float
    average_tool_call_count: float
    average_task_step_count: float
    model_calls: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    token_cost: float | None = None
