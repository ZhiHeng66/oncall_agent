"""Structured decisions used inside the Task Agent."""

from typing import Literal

from pydantic import BaseModel, Field


class TaskPlan(BaseModel):
    steps: list[str] = Field(default_factory=list)


class StepRoute(BaseModel):
    target: Literal["knowledge", "data", "direct"]
    objective: str
    expected_output: str = "完成当前步骤"
    preferred_tools: list[str] = Field(default_factory=list)


class ReplanDecision(BaseModel):
    action: Literal["continue", "replan", "respond"]
    new_steps: list[str] = Field(default_factory=list)
    response: str = ""
