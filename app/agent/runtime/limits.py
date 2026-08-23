"""Typed access to hard execution limits shared by workflow code."""

from dataclasses import dataclass

from app.config import Settings, config


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    supervisor_max_rounds: int
    task_max_steps: int
    reviewer_max_retries: int
    agent_timeout_seconds: float
    tool_timeout_seconds: float
    tool_max_retries: int
    graph_recursion_limit: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "RuntimeLimits":
        values = cls(
            supervisor_max_rounds=settings.supervisor_max_rounds,
            task_max_steps=settings.task_max_steps,
            reviewer_max_retries=settings.reviewer_max_retries,
            agent_timeout_seconds=settings.agent_timeout_seconds,
            tool_timeout_seconds=settings.tool_timeout_seconds,
            tool_max_retries=settings.tool_max_retries,
            graph_recursion_limit=settings.graph_recursion_limit,
        )
        if any(
            value <= 0
            for value in (
                values.supervisor_max_rounds,
                values.task_max_steps,
                values.agent_timeout_seconds,
                values.tool_timeout_seconds,
                values.tool_max_retries,
                values.graph_recursion_limit,
            )
        ):
            raise ValueError("Agent runtime limits must be positive")
        if values.reviewer_max_retries not in {0, 1}:
            raise ValueError("Reviewer retry limit must be zero or one")
        return values


runtime_limits = RuntimeLimits.from_settings(config)
