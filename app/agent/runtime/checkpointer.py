"""Factory for workflow checkpointers.

M1 intentionally supports only the in-memory backend. A persistent backend is
deferred until the multi-agent execution path is stable.
"""

from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from app.config import Settings, config

CheckpointerBackend = Literal["memory"]


class CheckpointerFactory:
    """Create an isolated checkpointer for each service or graph."""

    def __init__(self, settings: Settings = config) -> None:
        self._settings = settings

    def create(self, backend: CheckpointerBackend | None = None) -> BaseCheckpointSaver:
        selected = backend or self._settings.checkpointer_backend
        if selected != "memory":
            raise ValueError(f"Unsupported checkpointer backend: {selected}")
        return MemorySaver()


checkpointer_factory = CheckpointerFactory()
