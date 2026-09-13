"""Lifecycle-aware factory for supported LangGraph checkpointers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import Settings, config

CheckpointerBackend = Literal["memory", "sqlite", "postgres"]
PostgresPool = AsyncConnectionPool[AsyncConnection[dict[str, Any]]]


def create_postgres_pool(settings: Settings) -> PostgresPool:
    """Create a closed pool configured for LangGraph and application JSON queries."""

    return cast(
        PostgresPool,
        AsyncConnectionPool(
            conninfo=settings.database_url,
            min_size=settings.postgres_pool_min_size,
            max_size=settings.postgres_pool_max_size,
            timeout=settings.postgres_connect_timeout_seconds,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        ),
    )


class CheckpointerFactory:
    """Create an isolated checkpointer for each service or graph."""

    def __init__(self, settings: Settings = config) -> None:
        self._settings = settings

    def create_memory(self) -> BaseCheckpointSaver:
        """Return an isolated saver suitable for unit tests and import-time defaults."""

        return MemorySaver()

    @staticmethod
    def create_postgres(pool: PostgresPool) -> BaseCheckpointSaver:
        """Bind the official asynchronous PostgreSQL saver to a managed pool."""

        return AsyncPostgresSaver(pool)

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
        if selected == "sqlite":
            path = Path(self._settings.checkpoint_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
                await saver.setup()
                yield saver
            return
        if selected == "postgres":
            pool = create_postgres_pool(self._settings)
            await pool.open(wait=True)
            try:
                yield self.create_postgres(pool)
            finally:
                await pool.close()
            return
        raise ValueError(f"Unsupported checkpointer backend: {selected}")


checkpointer_factory = CheckpointerFactory()
