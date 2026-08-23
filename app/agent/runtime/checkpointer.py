"""Lifecycle-aware factory for memory and local SQLite checkpointers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import Settings, config

CheckpointerBackend = Literal["memory", "sqlite"]


class CheckpointerFactory:
    """Create an isolated checkpointer for each service or graph."""

    def __init__(self, settings: Settings = config) -> None:
        self._settings = settings

    def create_memory(self) -> BaseCheckpointSaver:
        """Return an isolated saver suitable for unit tests and import-time defaults."""

        return MemorySaver()

    @asynccontextmanager
    async def open(
        self,
        backend: CheckpointerBackend | None = None,
    ) -> AsyncIterator[BaseCheckpointSaver]:
        """Open a saver and keep its resources alive for the application lifespan."""

        selected = backend or self._settings.checkpointer_backend
        if selected == "memory":
            yield MemorySaver()
            return
        if selected != "sqlite":
            raise ValueError(f"Unsupported checkpointer backend: {selected}")

        path = Path(self._settings.checkpoint_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            await saver.setup()
            yield saver


checkpointer_factory = CheckpointerFactory()
