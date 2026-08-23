"""Deprecated compatibility export for the generic Task Agent replanner node."""

from app.agent.specialists.task.graph import task_agent
from app.agent.specialists.task.schemas import ReplanDecision as Act

replanner = task_agent.replan_node

__all__ = ["Act", "replanner"]
