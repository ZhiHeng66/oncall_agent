"""Ownership-scoped public run, trace, and conversation storage."""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol
from uuid import UUID

import aiosqlite
from psycopg.types.json import Jsonb

from app.agent.memory.contracts import PublicConversationTurn
from app.agent.runtime.checkpointer import PostgresPool
from app.models.agent import (
    AgentEventEnvelope,
    AgentMetricsResponse,
    AgentRunMetrics,
    AgentRunResponse,
    AgentRunTraceResponse,
    AgentThreadResponse,
)


class RunStore(Protocol):
    async def setup(self) -> None: ...
    async def save(self, owner_user_id: UUID, request_message: str, response: AgentRunResponse,
                   events: Sequence[AgentEventEnvelope], metrics: AgentRunMetrics) -> None: ...
    async def get_run(self, owner_user_id: UUID, run_id: str) -> AgentRunResponse | None: ...
    async def get_trace(self, owner_user_id: UUID, run_id: str) -> AgentRunTraceResponse | None: ...
    async def get_thread(self, owner_user_id: UUID, thread_id: str) -> AgentThreadResponse: ...
    async def get_recent_thread(self, owner_user_id: UUID, thread_id: str,
                                limit: int) -> list[PublicConversationTurn]: ...
    async def delete_thread(self, owner_user_id: UUID, thread_id: str) -> list[str]: ...
    async def metrics(self, owner_user_id: UUID | None = None) -> AgentMetricsResponse: ...


class MemoryRunStore:
    """In-process implementation with the same ownership contract as PostgreSQL."""

    def __init__(self) -> None:
        self._runs: dict[str, tuple[UUID, AgentRunResponse]] = {}
        self._requests: dict[str, str] = {}
        self._events: dict[str, list[AgentEventEnvelope]] = {}
        self._metrics: dict[str, AgentRunMetrics] = {}
        self._thread_runs: dict[tuple[UUID, str], list[str]] = {}
        self._lock = asyncio.Lock()

    async def setup(self) -> None:
        return None

    async def save(self, owner_user_id, request_message, response, events, metrics) -> None:
        async with self._lock:
            previous = self._runs.get(response.run_id)
            if previous and previous[1].thread_id != response.thread_id:
                self._thread_runs.get((previous[0], previous[1].thread_id), []).remove(response.run_id)
            self._runs[response.run_id] = (owner_user_id, response)
            self._requests[response.run_id] = request_message
            self._events[response.run_id] = list(events)
            self._metrics[response.run_id] = metrics
            run_ids = self._thread_runs.setdefault((owner_user_id, response.thread_id), [])
            if response.run_id not in run_ids:
                run_ids.append(response.run_id)

    async def get_run(self, owner_user_id, run_id):
        async with self._lock:
            item = self._runs.get(run_id)
            return item[1] if item and item[0] == owner_user_id else None

    async def get_trace(self, owner_user_id, run_id):
        async with self._lock:
            item = self._runs.get(run_id)
            if item is None or item[0] != owner_user_id:
                return None
            return AgentRunTraceResponse(
                run=item[1], events=list(self._events[run_id]), metrics=self._metrics[run_id]
            )

    async def get_thread(self, owner_user_id, thread_id):
        async with self._lock:
            run_ids = list(self._thread_runs.get((owner_user_id, thread_id), []))
            history = [turn for run_id in run_ids for turn in (
                {"role": "user", "content": self._requests[run_id]},
                {"role": "assistant", "content": self._runs[run_id][1].answer},
            )]
            return AgentThreadResponse(thread_id=thread_id, run_ids=run_ids, history=history)

    async def get_recent_thread(self, owner_user_id, thread_id, limit):
        if limit <= 0:
            return []
        async with self._lock:
            return [turn for run_id in self._thread_runs.get((owner_user_id, thread_id), [])[-limit:]
                    for turn in (
                        PublicConversationTurn(role="user", content=self._requests[run_id]),
                        PublicConversationTurn(role="assistant", content=self._runs[run_id][1].answer),
                    )]

    async def delete_thread(self, owner_user_id, thread_id):
        async with self._lock:
            run_ids = self._thread_runs.pop((owner_user_id, thread_id), [])
            for run_id in run_ids:
                self._runs.pop(run_id, None)
                self._requests.pop(run_id, None)
                self._events.pop(run_id, None)
                self._metrics.pop(run_id, None)
            return run_ids

    async def metrics(self, owner_user_id=None):
        async with self._lock:
            values = [self._metrics[run_id] for run_id, (owner, _) in self._runs.items()
                      if owner_user_id is None or owner == owner_user_id]
            return aggregate_metrics(values)


class SqliteRunStore:
    """SQLite development store which enforces ownership in each data query."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    async def setup(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._path) as db:
            await db.executescript("""
            CREATE TABLE IF NOT EXISTS agent_runs (
                run_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL,
                thread_id TEXT NOT NULL, request_message TEXT NOT NULL,
                response_json TEXT NOT NULL, metrics_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS agent_events (
                event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, sequence_no INTEGER NOT NULL,
                envelope_json TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES agent_runs(run_id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS idx_agent_events_run ON agent_events(run_id, sequence_no);
            """)
            columns = {row[1] for row in await (await db.execute("PRAGMA table_info(agent_runs)")).fetchall()}
            if "owner_user_id" not in columns:
                await db.execute(
                    "ALTER TABLE agent_runs ADD COLUMN owner_user_id TEXT NOT NULL DEFAULT 'legacy-system'"
                )
            await db.execute(
                """CREATE INDEX IF NOT EXISTS idx_agent_runs_owner_thread
                ON agent_runs(owner_user_id, thread_id, created_at, run_id)"""
            )
            await db.commit()

    async def save(self, owner_user_id, request_message, response, events, metrics):
        async with aiosqlite.connect(self._path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute(
                """INSERT OR REPLACE INTO agent_runs
                (run_id, owner_user_id, thread_id, request_message, response_json, metrics_json)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (response.run_id, str(owner_user_id), response.thread_id, request_message,
                 response.model_dump_json(), metrics.model_dump_json()),
            )
            await db.execute("DELETE FROM agent_events WHERE run_id = ?", (response.run_id,))
            await db.executemany(
                "INSERT INTO agent_events (event_id, run_id, sequence_no, envelope_json) VALUES (?, ?, ?, ?)",
                [(event.event_id, response.run_id, index, event.model_dump_json())
                 for index, event in enumerate(events)],
            )
            await db.commit()

    async def _one(self, owner, run_id, trace=False):
        async with aiosqlite.connect(self._path) as db:
            row = await (await db.execute(
                "SELECT response_json, metrics_json FROM agent_runs WHERE run_id = ? AND owner_user_id = ?",
                (run_id, str(owner)),
            )).fetchone()
            if row is None:
                return None
            if not trace:
                return AgentRunResponse.model_validate_json(row[0])
            events = await (await db.execute(
                "SELECT envelope_json FROM agent_events WHERE run_id = ? ORDER BY sequence_no", (run_id,)
            )).fetchall()
        return AgentRunTraceResponse(
            run=AgentRunResponse.model_validate_json(row[0]),
            events=[AgentEventEnvelope.model_validate_json(item[0]) for item in events],
            metrics=AgentRunMetrics.model_validate_json(row[1]),
        )

    async def get_run(self, owner_user_id, run_id):
        return await self._one(owner_user_id, run_id)

    async def get_trace(self, owner_user_id, run_id):
        return await self._one(owner_user_id, run_id, True)

    async def get_thread(self, owner_user_id, thread_id):
        async with aiosqlite.connect(self._path) as db:
            rows = await (await db.execute(
                """SELECT run_id, request_message, response_json FROM agent_runs
                WHERE owner_user_id = ? AND thread_id = ? ORDER BY created_at, run_id""",
                (str(owner_user_id), thread_id),
            )).fetchall()
        return thread_response(thread_id, rows)

    async def get_recent_thread(self, owner_user_id, thread_id, limit):
        if limit <= 0:
            return []
        async with aiosqlite.connect(self._path) as db:
            rows = list(reversed(await (await db.execute(
                """SELECT request_message, response_json FROM agent_runs
                WHERE owner_user_id = ? AND thread_id = ?
                ORDER BY created_at DESC, run_id DESC LIMIT ?""",
                (str(owner_user_id), thread_id, limit),
            )).fetchall()))
        return conversation_turns(rows)

    async def delete_thread(self, owner_user_id, thread_id):
        async with aiosqlite.connect(self._path) as db:
            rows = await (await db.execute(
                "SELECT run_id FROM agent_runs WHERE owner_user_id = ? AND thread_id = ?",
                (str(owner_user_id), thread_id),
            )).fetchall()
            await db.execute(
                "DELETE FROM agent_runs WHERE owner_user_id = ? AND thread_id = ?",
                (str(owner_user_id), thread_id),
            )
            await db.commit()
        return [item[0] for item in rows]

    async def metrics(self, owner_user_id=None):
        async with aiosqlite.connect(self._path) as db:
            if owner_user_id is None:
                cursor = await db.execute("SELECT metrics_json FROM agent_runs")
            else:
                cursor = await db.execute(
                    "SELECT metrics_json FROM agent_runs WHERE owner_user_id = ?", (str(owner_user_id),)
                )
            rows = await cursor.fetchall()
        return aggregate_metrics([AgentRunMetrics.model_validate_json(item[0]) for item in rows])


class PostgresRunStore:
    """PostgreSQL implementation; every public lookup is owner-scoped in SQL."""

    REQUIRED_TABLES = ("agent_runs", "agent_events", "agent_threads")

    def __init__(self, pool: PostgresPool) -> None:
        self._pool = pool

    async def setup(self) -> None:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT " + ", ".join(f"to_regclass('{table}') AS {table}" for table in self.REQUIRED_TABLES)
                )
                row = await cursor.fetchone()
        if row is None or any(row[table] is None for table in self.REQUIRED_TABLES):
            raise RuntimeError("PostgreSQL run store schema is not initialized; run the database upgrade command")

    async def save(self, owner_user_id, request_message, response, events, metrics):
        async with self._pool.connection() as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """INSERT INTO agent_runs
                        (run_id, owner_user_id, thread_id, request_message, response_json, metrics_json)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (run_id) DO UPDATE SET
                        owner_user_id = EXCLUDED.owner_user_id, thread_id = EXCLUDED.thread_id,
                        request_message = EXCLUDED.request_message, response_json = EXCLUDED.response_json,
                        metrics_json = EXCLUDED.metrics_json""",
                        (response.run_id, owner_user_id, response.thread_id, request_message,
                         Jsonb(response.model_dump(mode="json")), Jsonb(metrics.model_dump(mode="json"))),
                    )
                    await cursor.execute("DELETE FROM agent_events WHERE run_id = %s", (response.run_id,))
                    await cursor.executemany(
                        """INSERT INTO agent_events (event_id, run_id, sequence_no, envelope_json)
                        VALUES (%s, %s, %s, %s)""",
                        [(event.event_id, response.run_id, index, Jsonb(event.model_dump(mode="json")))
                         for index, event in enumerate(events)],
                    )

    async def get_run(self, owner_user_id, run_id):
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT response_json FROM agent_runs WHERE run_id = %s AND owner_user_id = %s",
                    (run_id, owner_user_id),
                )
                row = await cursor.fetchone()
        return AgentRunResponse.model_validate(row["response_json"]) if row else None

    async def get_trace(self, owner_user_id, run_id):
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """SELECT response_json, metrics_json FROM agent_runs
                    WHERE run_id = %s AND owner_user_id = %s""", (run_id, owner_user_id)
                )
                run = await cursor.fetchone()
                if run is None:
                    return None
                await cursor.execute(
                    "SELECT envelope_json FROM agent_events WHERE run_id = %s ORDER BY sequence_no", (run_id,)
                )
                events = await cursor.fetchall()
        return AgentRunTraceResponse(
            run=AgentRunResponse.model_validate(run["response_json"]),
            events=[AgentEventEnvelope.model_validate(item["envelope_json"]) for item in events],
            metrics=AgentRunMetrics.model_validate(run["metrics_json"]),
        )

    async def get_thread(self, owner_user_id, thread_id):
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """SELECT run_id, request_message, response_json FROM agent_runs
                    WHERE owner_user_id = %s AND thread_id = %s ORDER BY created_at, run_id""",
                    (owner_user_id, thread_id),
                )
                rows = await cursor.fetchall()
        return thread_response(thread_id, rows, mapping=True)

    async def get_recent_thread(self, owner_user_id, thread_id, limit):
        if limit <= 0:
            return []
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """SELECT request_message, response_json FROM agent_runs
                    WHERE owner_user_id = %s AND thread_id = %s
                    ORDER BY created_at DESC, run_id DESC LIMIT %s""",
                    (owner_user_id, thread_id, limit),
                )
                rows = list(reversed(await cursor.fetchall()))
        return conversation_turns(rows, mapping=True)

    async def delete_thread(self, owner_user_id, thread_id):
        async with self._pool.connection() as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """DELETE FROM agent_runs WHERE owner_user_id = %s AND thread_id = %s
                        RETURNING run_id""", (owner_user_id, thread_id)
                    )
                    rows = await cursor.fetchall()
        return [item["run_id"] for item in rows]

    async def metrics(self, owner_user_id=None):
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                if owner_user_id is None:
                    await cursor.execute("SELECT metrics_json FROM agent_runs")
                else:
                    await cursor.execute(
                        "SELECT metrics_json FROM agent_runs WHERE owner_user_id = %s", (owner_user_id,)
                    )
                rows = await cursor.fetchall()
        return aggregate_metrics([AgentRunMetrics.model_validate(item["metrics_json"]) for item in rows])


def conversation_turns(rows, mapping: bool = False) -> list[PublicConversationTurn]:
    return [
        turn
        for row in rows
        for message, response in [(
            row["request_message"] if mapping else row[0],
            row["response_json"] if mapping else row[1],
        )]
        for turn in (
            PublicConversationTurn(role="user", content=message),
            PublicConversationTurn(role="assistant", content=AgentRunResponse.model_validate(response).answer
                                   if mapping else AgentRunResponse.model_validate_json(response).answer),
        )
    ]


def thread_response(thread_id, rows, mapping: bool = False) -> AgentThreadResponse:
    unpacked = [
        (row["run_id"], row["request_message"], row["response_json"]) if mapping else row
        for row in rows
    ]
    return AgentThreadResponse(
        thread_id=thread_id,
        run_ids=[row[0] for row in unpacked],
        history=[
            item for _, message, response in unpacked for item in (
                {"role": "user", "content": message},
                {"role": "assistant", "content": AgentRunResponse.model_validate(response).answer
                 if mapping else AgentRunResponse.model_validate_json(response).answer},
            )
        ],
    )


def aggregate_metrics(values: list[AgentRunMetrics]) -> AgentMetricsResponse:
    count = len(values)
    return AgentMetricsResponse(
        total_runs=count,
        failure_rate=sum(item.failed for item in values) / count if count else 0,
        timeout_rate=sum(item.timed_out for item in values) / count if count else 0,
        average_duration_ms=sum(item.duration_ms for item in values) / count if count else 0,
        average_dispatch_count=sum(item.dispatch_count for item in values) / count if count else 0,
        average_tool_call_count=sum(item.tool_call_count for item in values) / count if count else 0,
        average_task_step_count=sum(item.task_step_count for item in values) / count if count else 0,
    )
