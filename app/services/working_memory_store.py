"""PostgreSQL persistence for bounded cross-turn working-memory snapshots."""

from typing import Protocol

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app.agent.memory.contracts import WorkingMemoryContext
from app.agent.runtime.checkpointer import PostgresPool


class WorkingMemoryStore(Protocol):
    async def setup(self) -> None: ...

    async def load(self, thread_id: str) -> WorkingMemoryContext | None: ...

    async def create(self, context: WorkingMemoryContext) -> bool: ...

    async def compare_and_swap(
        self,
        context: WorkingMemoryContext,
        expected_version: int,
    ) -> bool: ...

    async def delete_thread(self, thread_id: str) -> None: ...


class PostgresWorkingMemoryStore:
    """Store one materialized memory snapshot per business thread."""

    TABLE_NAME = "agent_working_memories"

    def __init__(self, pool: PostgresPool) -> None:
        self._pool = pool

    async def setup(self) -> None:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT to_regclass(%s) AS working_memory",
                    (self.TABLE_NAME,),
                )
                row = await cursor.fetchone()
        if row is None or row["working_memory"] is None:
            raise RuntimeError(
                "PostgreSQL working-memory schema is not initialized; "
                "run the database upgrade command"
            )

    async def load(self, thread_id: str) -> WorkingMemoryContext | None:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """SELECT thread_id, owner_user_id, schema_version, version,
                    context_json, last_run_id, created_at, updated_at
                    FROM agent_working_memories WHERE thread_id = %s""",
                    (thread_id,),
                )
                row = await cursor.fetchone()
        if row is None:
            return None
        payload = dict(row["context_json"])
        payload.update(
            {
                "thread_id": row["thread_id"],
                "owner_user_id": row["owner_user_id"],
                "schema_version": row["schema_version"],
                "version": row["version"],
                "last_run_id": row["last_run_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        try:
            return WorkingMemoryContext.model_validate(payload)
        except ValidationError as exc:
            raise RuntimeError("Stored working-memory context is invalid") from exc

    async def create(self, context: WorkingMemoryContext) -> bool:
        context = self._validate_context(context)
        if context.version != 1:
            raise ValueError("new working-memory context must start at version 1")
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """INSERT INTO agent_working_memories
                    (thread_id, owner_user_id, schema_version, version, context_json,
                     last_run_id, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (thread_id) DO NOTHING
                    RETURNING thread_id""",
                    (
                        context.thread_id,
                        context.owner_user_id,
                        context.schema_version,
                        context.version,
                        Jsonb(context.model_dump(mode="json")),
                        context.last_run_id,
                        context.created_at,
                        context.updated_at,
                    ),
                )
                row = await cursor.fetchone()
        return row is not None

    async def compare_and_swap(
        self,
        context: WorkingMemoryContext,
        expected_version: int,
    ) -> bool:
        context = self._validate_context(context)
        if context.version != expected_version + 1:
            raise ValueError("updated context version must equal expected_version + 1")
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """UPDATE agent_working_memories
                    SET schema_version = %s,
                        version = %s,
                        context_json = %s,
                        last_run_id = %s,
                        updated_at = %s
                    WHERE thread_id = %s AND owner_user_id = %s AND version = %s
                    RETURNING thread_id""",
                    (
                        context.schema_version,
                        context.version,
                        Jsonb(context.model_dump(mode="json")),
                        context.last_run_id,
                        context.updated_at,
                        context.thread_id,
                        context.owner_user_id,
                        expected_version,
                    ),
                )
                row = await cursor.fetchone()
        return row is not None

    async def delete_thread(self, thread_id: str) -> None:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM agent_working_memories WHERE thread_id = %s",
                    (thread_id,),
                )

    @staticmethod
    def _validate_context(context: WorkingMemoryContext) -> WorkingMemoryContext:
        return WorkingMemoryContext.model_validate(context.model_dump(mode="python"))
