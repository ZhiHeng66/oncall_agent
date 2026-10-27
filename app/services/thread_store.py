"""Ownership-aware thread directory for the unified Agent API."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.agent.runtime.checkpointer import PostgresPool


class AgentThread(BaseModel):
    thread_id: str
    owner_user_id: UUID
    title: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class InMemoryThreadStore:
    def __init__(self) -> None:
        self._threads: dict[str, AgentThread] = {}
        self._lock = asyncio.Lock()

    async def create(self, owner_user_id: UUID, title: str = "") -> AgentThread:
        async with self._lock:
            thread = AgentThread(thread_id=str(uuid4()), owner_user_id=owner_user_id, title=title)
            self._threads[thread.thread_id] = thread
            return thread

    async def get_owned(self, owner_user_id: UUID, thread_id: str) -> AgentThread | None:
        async with self._lock:
            thread = self._threads.get(thread_id)
            return thread if thread and thread.owner_user_id == owner_user_id else None

    async def delete_owned(self, owner_user_id: UUID, thread_id: str) -> bool:
        async with self._lock:
            thread = self._threads.get(thread_id)
            if thread is None or thread.owner_user_id != owner_user_id:
                return False
            del self._threads[thread_id]
            return True

    async def touch_owned(self, owner_user_id: UUID, thread_id: str) -> bool:
        async with self._lock:
            thread = self._threads.get(thread_id)
            if thread is None or thread.owner_user_id != owner_user_id:
                return False
            thread.updated_at = datetime.now(UTC)
            return True

    async def list_owned(
        self, owner_user_id: UUID, *, limit: int = 50, before: datetime | None = None
    ) -> list[AgentThread]:
        async with self._lock:
            values = [item for item in self._threads.values() if item.owner_user_id == owner_user_id]
            if before is not None:
                values = [item for item in values if item.updated_at < before]
            return sorted(values, key=lambda item: (item.updated_at, item.thread_id), reverse=True)[:limit]


class PostgresThreadStore:
    def __init__(self, pool: PostgresPool) -> None:
        self._pool = pool

    async def create(self, owner_user_id: UUID, title: str = "") -> AgentThread:
        thread_id = str(uuid4())
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """INSERT INTO agent_threads (thread_id, owner_user_id, title)
                    VALUES (%s, %s, %s) RETURNING thread_id, owner_user_id, title, created_at, updated_at""",
                    (thread_id, owner_user_id, title),
                )
                row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Thread creation did not return a record")
        return AgentThread.model_validate(row)

    async def get_owned(self, owner_user_id: UUID, thread_id: str) -> AgentThread | None:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """SELECT thread_id, owner_user_id, title, created_at, updated_at FROM agent_threads
                    WHERE thread_id = %s AND owner_user_id = %s""",
                    (thread_id, owner_user_id),
                )
                row = await cursor.fetchone()
        return AgentThread.model_validate(row) if row else None

    async def delete_owned(self, owner_user_id: UUID, thread_id: str) -> bool:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM agent_threads WHERE thread_id = %s AND owner_user_id = %s RETURNING thread_id",
                    (thread_id, owner_user_id),
                )
                row = await cursor.fetchone()
        return row is not None

    async def touch_owned(self, owner_user_id: UUID, thread_id: str) -> bool:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """UPDATE agent_threads SET updated_at = CURRENT_TIMESTAMP
                    WHERE thread_id = %s AND owner_user_id = %s RETURNING thread_id""",
                    (thread_id, owner_user_id),
                )
                return await cursor.fetchone() is not None

    async def list_owned(
        self, owner_user_id: UUID, *, limit: int = 50, before: datetime | None = None
    ) -> list[AgentThread]:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                if before is None:
                    await cursor.execute(
                        """SELECT thread_id, owner_user_id, title, created_at, updated_at
                        FROM agent_threads WHERE owner_user_id = %s
                        ORDER BY updated_at DESC, thread_id DESC LIMIT %s""",
                        (owner_user_id, limit),
                    )
                else:
                    await cursor.execute(
                        """SELECT thread_id, owner_user_id, title, created_at, updated_at
                        FROM agent_threads WHERE owner_user_id = %s AND updated_at < %s
                        ORDER BY updated_at DESC, thread_id DESC LIMIT %s""",
                        (owner_user_id, before, limit),
                    )
                rows = await cursor.fetchall()
        return [AgentThread.model_validate(row) for row in rows]
