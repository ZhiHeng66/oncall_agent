"""Deprecated compatibility export for the generic Task Agent executor node."""

from app.agent.specialists.task.graph import task_agent

executor = task_agent.execute_node

__all__ = ["executor"]
