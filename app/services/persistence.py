"""Create matching Checkpointer and RunStore resources for an application lifespan."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.runtime.checkpointer import (
    CheckpointerFactory,
    PostgresPool,
    create_postgres_pool,
)
from app.config import Settings, config
from app.services.run_store import MemoryRunStore, PostgresRunStore, RunStore, SqliteRunStore
from app.services.working_memory_store import PostgresWorkingMemoryStore, WorkingMemoryStore


@dataclass(frozen=True)
class PersistenceResources:
    checkpointer: BaseCheckpointSaver
    run_store: RunStore
    working_memory_store: WorkingMemoryStore | None


class PersistenceFactory:
    """Open a consistent persistence pair without leaking backend details to services."""

    def __init__(
        self,
        settings: Settings = config,
        checkpointers: CheckpointerFactory | None = None,
    ) -> None:
        self._settings = settings
        self._checkpointers = checkpointers or CheckpointerFactory(settings)

    @asynccontextmanager
    async def open(self) -> AsyncIterator[PersistenceResources]:
        backend = self._settings.checkpointer_backend
        if backend == "memory":
            memory_store = MemoryRunStore()
            await memory_store.setup()
            yield PersistenceResources(
                checkpointer=self._checkpointers.create_memory(),
                run_store=memory_store,
                working_memory_store=None,
            )
            return
        if backend == "sqlite":
            async with self._checkpointers.open("sqlite") as checkpointer:
                sqlite_store = SqliteRunStore(self._settings.checkpoint_path)
                await sqlite_store.setup()
                yield PersistenceResources(
                    checkpointer=checkpointer,
                    run_store=sqlite_store,
                    working_memory_store=None,
                )
            return

        pool = create_postgres_pool(self._settings)
        await pool.open(wait=True)
        try:
            await self._verify_checkpoint_schema(pool)
            postgres_store = PostgresRunStore(pool)
            await postgres_store.setup()
            working_memory_store: WorkingMemoryStore | None = None
            if self._settings.working_memory_enabled:
                postgres_memory_store = PostgresWorkingMemoryStore(pool)
                await postgres_memory_store.setup()
                working_memory_store = postgres_memory_store
            yield PersistenceResources(
                checkpointer=self._checkpointers.create_postgres(pool),
                run_store=postgres_store,
                working_memory_store=working_memory_store,
            )
        finally:
            await pool.close()

    @staticmethod
    async def _verify_checkpoint_schema(pool: PostgresPool) -> None:
        required = (
            "checkpoint_migrations",
            "checkpoints",
            "checkpoint_blobs",
            "checkpoint_writes",
        )
        async with pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT "
                    + ", ".join(f"to_regclass('{table}') AS {table}" for table in required)
                )
                row = await cursor.fetchone()
        if row is None or any(row[table] is None for table in required):
            raise RuntimeError(
                "PostgreSQL checkpoint schema is not initialized; run the database upgrade command"
            )


persistence_factory = PersistenceFactory()
