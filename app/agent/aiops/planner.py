"""Deprecated compatibility export for the generic Task Agent planner node."""

from app.agent.specialists.task.graph import task_agent
from app.agent.specialists.task.schemas import TaskPlan as Plan

planner = task_agent.plan_node

__all__ = ["Plan", "planner"]
