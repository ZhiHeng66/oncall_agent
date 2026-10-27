"""Copy public run and event records from SQLite into PostgreSQL."""

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import aiosqlite

from app.agent.runtime.checkpointer import create_postgres_pool
from app.config import Settings
from app.models.agent import AgentEventEnvelope, AgentRunMetrics, AgentRunResponse
from app.services.run_store import PostgresRunStore, RunStore

LEGACY_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


@dataclass(frozen=True)
class MigrationSummary:
    runs: int
    events: int


async def migrate_sqlite_run_store(source_path: Path, target: RunStore) -> MigrationSummary:
    """Upsert validated public records without modifying the source database."""

    run_count = 0
    event_count = 0
    source_uri = f"file:{source_path}?mode=ro"
    async with aiosqlite.connect(source_uri, uri=True) as source:
        cursor = await source.execute(
            """SELECT run_id, request_message, response_json, metrics_json
            FROM agent_runs ORDER BY created_at, run_id"""
        )
        run_rows = await cursor.fetchall()
        for run_id, request_message, raw_response, raw_metrics in run_rows:
            cursor = await source.execute(
                """SELECT envelope_json FROM agent_events
                WHERE run_id = ? ORDER BY sequence_no""",
                (run_id,),
            )
            event_rows = await cursor.fetchall()
            response = AgentRunResponse.model_validate_json(raw_response)
            metrics = AgentRunMetrics.model_validate_json(raw_metrics)
            events = [AgentEventEnvelope.model_validate_json(row[0]) for row in event_rows]
            await target.save(LEGACY_USER_ID, request_message, response, events, metrics)
            run_count += 1
            event_count += len(events)
    return MigrationSummary(runs=run_count, events=event_count)


async def run_migration(source_path: Path, settings: Settings) -> MigrationSummary:
    pool = create_postgres_pool(settings)
    await pool.open(wait=True)
    try:
        store = PostgresRunStore(pool)
        await store.setup()
        return await migrate_sqlite_run_store(source_path, store)
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-path", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings()
    if not settings.database_url.strip():
        parser.error("DATABASE_URL is required")
    if not args.sqlite_path.is_file():
        parser.error("SQLite source file does not exist")
    summary = asyncio.run(run_migration(args.sqlite_path, settings))
    print(f"Migrated runs={summary.runs} events={summary.events}")


if __name__ == "__main__":
    main()
