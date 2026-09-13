"""Run, thread and trace storage used by the public Agent service."""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import aiosqlite
from psycopg.types.json import Jsonb

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

    async def save(
        self,
        request_message: str,
        response: AgentRunResponse,
        events: Sequence[AgentEventEnvelope],
        metrics: AgentRunMetrics,
    ) -> None: ...

    async def get_run(self, run_id: str) -> AgentRunResponse | None: ...

    async def get_trace(self, run_id: str) -> AgentRunTraceResponse | None: ...

    async def get_thread(self, thread_id: str) -> AgentThreadResponse: ...

    async def delete_thread(self, thread_id: str) -> list[str]: ...

    async def metrics(self) -> AgentMetricsResponse: ...


class MemoryRunStore:
    """Isolated in-memory store for tests and explicit memory deployments."""

    def __init__(self) -> None:
        self._runs: dict[str, AgentRunResponse] = {}
        self._requests: dict[str, str] = {}
        self._events: dict[str, list[AgentEventEnvelope]] = {}
        self._metrics: dict[str, AgentRunMetrics] = {}
        self._thread_runs: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    async def setup(self) -> None:
        return None

    async def save(self, request_message, response, events, metrics) -> None:
        async with self._lock:
            previous = self._runs.get(response.run_id)
            if previous is not None and previous.thread_id != response.thread_id:
                self._thread_runs[previous.thread_id].remove(response.run_id)
            self._runs[response.run_id] = response
            self._requests[response.run_id] = request_message
            self._events[response.run_id] = list(events)
            self._metrics[response.run_id] = metrics
            thread_runs = self._thread_runs.setdefault(response.thread_id, [])
            if response.run_id not in thread_runs:
                thread_runs.append(response.run_id)

    async def get_run(self, run_id: str) -> AgentRunResponse | None:
        async with self._lock:
            return self._runs.get(run_id)

    async def get_trace(self, run_id: str) -> AgentRunTraceResponse | None:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            return AgentRunTraceResponse(
                run=run,
                events=list(self._events[run_id]),
                metrics=self._metrics[run_id],
            )

    async def get_thread(self, thread_id: str) -> AgentThreadResponse:
        async with self._lock:
            run_ids = list(self._thread_runs.get(thread_id, []))
            history: list[dict[str, str]] = []
            for run_id in run_ids:
                history.extend(
                    [
                        {"role": "user", "content": self._requests[run_id]},
                        {"role": "assistant", "content": self._runs[run_id].answer},
                    ]
                )
            return AgentThreadResponse(
                thread_id=thread_id,
                run_ids=run_ids,
                history=history,
            )

    async def delete_thread(self, thread_id: str) -> list[str]:
        async with self._lock:
            run_ids = self._thread_runs.pop(thread_id, [])
            for run_id in run_ids:
                self._runs.pop(run_id, None)
                self._requests.pop(run_id, None)
                self._events.pop(run_id, None)
                self._metrics.pop(run_id, None)
            return run_ids

    async def metrics(self) -> AgentMetricsResponse:
        async with self._lock:
            return aggregate_metrics(list(self._metrics.values()))


class SqliteRunStore:
    """SQLite-backed public run index, separate from LangGraph checkpoint tables."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    async def setup(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    request_message TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_agent_runs_thread
                    ON agent_runs(thread_id, created_at, run_id);
                CREATE TABLE IF NOT EXISTS agent_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    sequence_no INTEGER NOT NULL,
                    envelope_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES agent_runs(run_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_agent_events_run
                    ON agent_events(run_id, sequence_no);
                """
            )
            await db.commit()

    async def save(self, request_message, response, events, metrics) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute(
                """INSERT OR REPLACE INTO agent_runs
                (run_id, thread_id, request_message, response_json, metrics_json)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    response.run_id,
                    response.thread_id,
                    request_message,
                    response.model_dump_json(),
                    metrics.model_dump_json(),
                ),
            )
            await db.execute("DELETE FROM agent_events WHERE run_id = ?", (response.run_id,))
            await db.executemany(
                """INSERT INTO agent_events
                (event_id, run_id, sequence_no, envelope_json) VALUES (?, ?, ?, ?)""",
                [
                    (event.event_id, response.run_id, index, event.model_dump_json())
                    for index, event in enumerate(events)
                ],
            )
            await db.commit()

    async def get_run(self, run_id: str) -> AgentRunResponse | None:
        async with aiosqlite.connect(self._path) as db:
            cursor = await db.execute(
                "SELECT response_json FROM agent_runs WHERE run_id = ?", (run_id,)
            )
            row = await cursor.fetchone()
        return AgentRunResponse.model_validate_json(row[0]) if row else None

    async def get_trace(self, run_id: str) -> AgentRunTraceResponse | None:
        async with aiosqlite.connect(self._path) as db:
            cursor = await db.execute(
                "SELECT response_json, metrics_json FROM agent_runs WHERE run_id = ?", (run_id,)
            )
            run_row = await cursor.fetchone()
            if run_row is None:
                return None
            cursor = await db.execute(
                """SELECT envelope_json FROM agent_events
                WHERE run_id = ? ORDER BY sequence_no""",
                (run_id,),
            )
            event_rows = await cursor.fetchall()
        return AgentRunTraceResponse(
            run=AgentRunResponse.model_validate_json(run_row[0]),
            events=[AgentEventEnvelope.model_validate_json(row[0]) for row in event_rows],
            metrics=AgentRunMetrics.model_validate_json(run_row[1]),
        )

    async def get_thread(self, thread_id: str) -> AgentThreadResponse:
        async with aiosqlite.connect(self._path) as db:
            cursor = await db.execute(
                """SELECT run_id, request_message, response_json FROM agent_runs
                WHERE thread_id = ? ORDER BY created_at, run_id""",
                (thread_id,),
            )
            rows = await cursor.fetchall()
        history: list[dict[str, str]] = []
        for _, message, raw_response in rows:
            response = AgentRunResponse.model_validate_json(raw_response)
            history.extend(
                [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": response.answer},
                ]
            )
        return AgentThreadResponse(
            thread_id=thread_id,
            run_ids=[row[0] for row in rows],
            history=history,
        )

    async def delete_thread(self, thread_id: str) -> list[str]:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            cursor = await db.execute(
                "SELECT run_id FROM agent_runs WHERE thread_id = ?", (thread_id,)
            )
            run_ids = [row[0] for row in await cursor.fetchall()]
            await db.execute("DELETE FROM agent_runs WHERE thread_id = ?", (thread_id,))
            await db.commit()
        return run_ids

    async def metrics(self) -> AgentMetricsResponse:
        async with aiosqlite.connect(self._path) as db:
            cursor = await db.execute("SELECT metrics_json FROM agent_runs")
            rows = await cursor.fetchall()
        return aggregate_metrics([AgentRunMetrics.model_validate_json(row[0]) for row in rows])


class PostgresRunStore:
    """PostgreSQL-backed public run index using a lifespan-managed connection pool."""

    REQUIRED_TABLES = ("agent_runs", "agent_events")

    def __init__(self, pool: PostgresPool) -> None:
        self._pool = pool

    async def setup(self) -> None:
        """Verify schema presence; schema creation is an explicit deployment step."""

        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT to_regclass('agent_runs') AS agent_runs, "
                    "to_regclass('agent_events') AS agent_events"
                )
                row = await cursor.fetchone()
        if row is None or any(row[table] is None for table in self.REQUIRED_TABLES):
            raise RuntimeError(
                "PostgreSQL run store schema is not initialized; run the database upgrade command"
            )

    async def save(self, request_message, response, events, metrics) -> None:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """INSERT INTO agent_runs
                        (run_id, thread_id, request_message, response_json, metrics_json)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (run_id) DO UPDATE SET
                            thread_id = EXCLUDED.thread_id,
                            request_message = EXCLUDED.request_message,
                            response_json = EXCLUDED.response_json,
                            metrics_json = EXCLUDED.metrics_json""",
                        (
                            response.run_id,
                            response.thread_id,
                            request_message,
                            Jsonb(response.model_dump(mode="json")),
                            Jsonb(metrics.model_dump(mode="json")),
                        ),
                    )
                    await cursor.execute(
                        "DELETE FROM agent_events WHERE run_id = %s", (response.run_id,)
                    )
                    await cursor.executemany(
                        """INSERT INTO agent_events
                        (event_id, run_id, sequence_no, envelope_json) VALUES (%s, %s, %s, %s)""",
                        [
                            (
                                event.event_id,
                                response.run_id,
                                index,
                                Jsonb(event.model_dump(mode="json")),
                            )
                            for index, event in enumerate(events)
                        ],
                    )

    async def get_run(self, run_id: str) -> AgentRunResponse | None:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT response_json FROM agent_runs WHERE run_id = %s", (run_id,)
                )
                row = await cursor.fetchone()
        return AgentRunResponse.model_validate(row["response_json"]) if row else None

    async def get_trace(self, run_id: str) -> AgentRunTraceResponse | None:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """SELECT response_json, metrics_json FROM agent_runs
                    WHERE run_id = %s""",
                    (run_id,),
                )
                run_row = await cursor.fetchone()
                if run_row is None:
                    return None
                await cursor.execute(
                    """SELECT envelope_json FROM agent_events
                    WHERE run_id = %s ORDER BY sequence_no""",
                    (run_id,),
                )
                event_rows = await cursor.fetchall()
        return AgentRunTraceResponse(
            run=AgentRunResponse.model_validate(run_row["response_json"]),
            events=[AgentEventEnvelope.model_validate(row["envelope_json"]) for row in event_rows],
            metrics=AgentRunMetrics.model_validate(run_row["metrics_json"]),
        )

    async def get_thread(self, thread_id: str) -> AgentThreadResponse:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """SELECT run_id, request_message, response_json FROM agent_runs
                    WHERE thread_id = %s ORDER BY created_at, run_id""",
                    (thread_id,),
                )
                rows = await cursor.fetchall()
        history: list[dict[str, str]] = []
        for row in rows:
            response = AgentRunResponse.model_validate(row["response_json"])
            history.extend(
                [
                    {"role": "user", "content": row["request_message"]},
                    {"role": "assistant", "content": response.answer},
                ]
            )
        return AgentThreadResponse(
            thread_id=thread_id,
            run_ids=[row["run_id"] for row in rows],
            history=history,
        )

    async def delete_thread(self, thread_id: str) -> list[str]:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        "SELECT run_id FROM agent_runs WHERE thread_id = %s ORDER BY run_id",
                        (thread_id,),
                    )
                    run_ids = [row["run_id"] for row in await cursor.fetchall()]
                    await cursor.execute(
                        "DELETE FROM agent_runs WHERE thread_id = %s", (thread_id,)
                    )
        return run_ids

    async def metrics(self) -> AgentMetricsResponse:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT metrics_json FROM agent_runs")
                rows = await cursor.fetchall()
        return aggregate_metrics(
            [AgentRunMetrics.model_validate(row["metrics_json"]) for row in rows]
        )


def aggregate_metrics(metrics: list[AgentRunMetrics]) -> AgentMetricsResponse:
    if not metrics:
        return AgentMetricsResponse(
            total_runs=0,
            failure_rate=0.0,
            timeout_rate=0.0,
            average_duration_ms=0.0,
            average_dispatch_count=0.0,
            average_tool_call_count=0.0,
            average_task_step_count=0.0,
        )
    count = len(metrics)
    return AgentMetricsResponse(
        total_runs=count,
        failure_rate=sum(item.failed for item in metrics) / count,
        timeout_rate=sum(item.timed_out for item in metrics) / count,
        average_duration_ms=sum(item.duration_ms for item in metrics) / count,
        average_dispatch_count=sum(item.dispatch_count for item in metrics) / count,
        average_tool_call_count=sum(item.tool_call_count for item in metrics) / count,
        average_task_step_count=sum(item.task_step_count for item in metrics) / count,
    )
