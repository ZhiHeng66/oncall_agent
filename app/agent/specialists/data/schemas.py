"""Data specialist schemas."""

from typing import Any

from pydantic import BaseModel, Field


class DataToolCall(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class DataToolPlan(BaseModel):
    calls: list[DataToolCall] = Field(default_factory=list)
