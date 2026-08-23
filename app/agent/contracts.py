"""Typed contracts exchanged between agents and workflow boundaries."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

AgentStatus = Literal["success", "partial", "failed"]
EvidenceSourceType = Literal["knowledge", "mcp", "tool"]
AgentTarget = Literal["knowledge", "data", "task"]
RetryTarget = Literal["none", "knowledge", "data", "task", "supervisor"]
RouteMode = Literal["direct", "single", "parallel", "complex"]
RunStatus = Literal["running", "completed", "partial", "failed"]


class AgentTask(BaseModel):
    task_id: str
    objective: str
    context: str = ""
    expected_output: str = "清晰、准确的任务结果"
    constraints: list[str] = Field(default_factory=list)
    preferred_tools: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    source_type: EvidenceSourceType
    source_name: str
    source_id: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    agent_name: str
    task_id: str
    status: AgentStatus
    summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class TaskStepResult(BaseModel):
    step: str
    status: AgentStatus
    summary: str
    delegated_agent: Literal["knowledge", "data", "direct", "unknown"]
    evidence: list[Evidence] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class RouteDecision(BaseModel):
    mode: RouteMode
    target_agents: list[AgentTarget] = Field(default_factory=list)
    tasks: list[AgentTask] = Field(default_factory=list)
    reason_summary: str
    direct_answer: str = ""

    @model_validator(mode="after")
    def validate_route_shape(self) -> "RouteDecision":
        if self.mode == "direct":
            if self.target_agents or self.tasks:
                raise ValueError("direct route cannot dispatch agents")
            return self
        if len(self.target_agents) != len(self.tasks):
            raise ValueError("each target agent must have exactly one task")
        if self.mode == "single" and (
            len(self.target_agents) != 1 or self.target_agents[0] == "task"
        ):
            raise ValueError("single route requires one knowledge or data target")
        if self.mode == "parallel" and (
            len(self.target_agents) < 2
            or len(set(self.target_agents)) != len(self.target_agents)
            or any(target == "task" for target in self.target_agents)
        ):
            raise ValueError("parallel route requires distinct read-only specialists")
        if self.mode == "complex" and self.target_agents != ["task"]:
            raise ValueError("complex route requires the Task Agent")
        return self


class ReviewResult(BaseModel):
    passed: bool
    completeness_score: float = Field(ge=0.0, le=1.0)
    groundedness_score: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)
    retry_target: RetryTarget = "none"
    retry_instruction: str = ""

    @model_validator(mode="after")
    def validate_retry(self) -> "ReviewResult":
        if self.passed and (self.retry_target != "none" or self.issues):
            raise ValueError("passed review cannot request retry or contain issues")
        if not self.passed and not self.issues:
            raise ValueError("failed review must describe at least one issue")
        return self


class DispatchRecord(BaseModel):
    round: int
    agent_name: AgentTarget
    task_id: str
    status: AgentStatus
    duration_ms: float = Field(ge=0.0)


class MultiAgentRunResult(BaseModel):
    run_id: str
    thread_id: str
    status: RunStatus
    final_answer: str
    route: RouteDecision | None = None
    agent_results: list[AgentResult] = Field(default_factory=list)
    review_history: list[ReviewResult] = Field(default_factory=list)
    dispatch_records: list[DispatchRecord] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    termination_reason: str
