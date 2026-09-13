import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg.errors import UniqueViolation

from app.agent.contracts import RouteDecision
from app.agent.graph import MultiAgentGraph
from app.cli.database import upgrade_application_schema, upgrade_checkpoint_schema
from app.cli.migrate_sqlite import migrate_sqlite_run_store
from app.config import Settings
from app.models.agent import AgentRunMetrics, AgentRunRequest, AgentRunResponse
from app.services.event_service import AgentEventService
from app.services.multi_agent_service import MultiAgentService
from app.services.persistence import PersistenceFactory
from app.services.run_store import SqliteRunStore

pytestmark = pytest.mark.postgres


class DirectSupervisor:
    async def route(self, user_query, previous_results, review, round_number):
        return RouteDecision(
            mode="direct",
            reason_summary="postgres integration route",
            direct_answer=f"shared answer: {user_query}",
        )


class PassThroughFinalResponse:
    async def generate(self, user_query, candidate_answer, *, event_sink=None):
        if event_sink is not None:
            event_sink(candidate_answer)
        return candidate_answer


@pytest.fixture(scope="module")
def postgres_settings() -> Iterator[Settings]:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    settings = Settings(
        _env_file=None,
        dashscope_api_key="",
        checkpointer_backend="postgres",
        database_url=database_url,
        postgres_pool_min_size=1,
        postgres_pool_max_size=5,
    )
    upgrade_application_schema(settings)
    asyncio.run(upgrade_checkpoint_schema(settings))
    yield settings


def build_service(settings: Settings) -> MultiAgentService:
    return MultiAgentService(
        runner=MultiAgentGraph(
            supervisor=DirectSupervisor(), final_response=PassThroughFinalResponse()
        ),
        events=AgentEventService(settings),
        settings=settings,
    )


async def test_two_service_instances_share_runs_and_checkpoint(postgres_settings) -> None:
    thread_id = f"postgres-thread-{uuid4()}"
    first = build_service(postgres_settings)
    second = build_service(postgres_settings)
    await first.startup()
    await second.startup()
    try:
        response = await first.run(
            AgentRunRequest(user_id="user", thread_id=thread_id, message="hello")
        )
        shared_run = await second.get_run(response.run_id)
        shared_thread = await second.get_thread(thread_id)
        graph_config = {"configurable": {"thread_id": f"{thread_id}:{response.run_id}"}}
        snapshot = await second._runner.graph.aget_state(graph_config)

        assert shared_run == response
        assert shared_thread.run_ids == [response.run_id]
        assert snapshot.values["run_id"] == response.run_id
    finally:
        await second.shutdown()
        await first.shutdown()

    reopened = build_service(postgres_settings)
    await reopened.startup()
    try:
        assert await reopened.get_run(response.run_id) == response
        assert (await reopened._runner.graph.aget_state(graph_config)).values["run_id"] == (
            response.run_id
        )
        deleted = await reopened.clear_thread(thread_id)
        assert deleted.deleted_runs == 1
        assert await reopened.get_run(response.run_id) is None
        assert not (await reopened._runner.graph.aget_state(graph_config)).values
    finally:
        await reopened.shutdown()


async def test_postgres_run_store_concurrent_writes_are_complete(postgres_settings) -> None:
    thread_id = f"concurrent-thread-{uuid4()}"
    async with PersistenceFactory(postgres_settings).open() as persistence:
        event_service = AgentEventService(postgres_settings)

        async def save(index: int) -> None:
            response = AgentRunResponse(
                run_id=f"{thread_id}-run-{index}",
                thread_id=thread_id,
                status="completed",
                answer=f"answer-{index}",
                termination_reason="completed",
            )
            event = event_service.create(
                run_id=response.run_id,
                thread_id=thread_id,
                event_type="run_completed",
                data={"status": "completed"},
            )
            await persistence.run_store.save(
                f"question-{index}",
                response,
                [event],
                AgentRunMetrics(duration_ms=float(index)),
            )

        await asyncio.gather(*(save(index) for index in range(12)))
        thread = await persistence.run_store.get_thread(thread_id)
        traces = await asyncio.gather(
            *(persistence.run_store.get_trace(run_id) for run_id in thread.run_ids)
        )

        assert len(thread.run_ids) == 12
        assert all(trace is not None and len(trace.events) == 1 for trace in traces)
        await persistence.run_store.delete_thread(thread_id)


async def test_postgres_run_store_upsert_replaces_events(postgres_settings) -> None:
    thread_id = f"upsert-thread-{uuid4()}"
    run_id = f"upsert-run-{uuid4()}"
    async with PersistenceFactory(postgres_settings).open() as persistence:
        event_service = AgentEventService(postgres_settings)
        first = AgentRunResponse(
            run_id=run_id,
            thread_id=thread_id,
            status="completed",
            answer="first",
            termination_reason="completed",
        )
        second = first.model_copy(update={"answer": "second"})
        first_event = event_service.create(
            run_id=run_id,
            thread_id=thread_id,
            event_type="run_started",
            data={},
        )
        second_event = event_service.create(
            run_id=run_id,
            thread_id=thread_id,
            event_type="run_completed",
            data={"status": "completed"},
        )

        await persistence.run_store.save(
            "first question", first, [first_event], AgentRunMetrics(duration_ms=1.0)
        )
        await persistence.run_store.save(
            "second question", second, [second_event], AgentRunMetrics(duration_ms=2.0)
        )
        trace = await persistence.run_store.get_trace(run_id)
        thread = await persistence.run_store.get_thread(thread_id)

        assert trace is not None
        assert trace.run.answer == "second"
        assert trace.events == [second_event]
        assert thread.run_ids == [run_id]
        assert thread.history == [
            {"role": "user", "content": "second question"},
            {"role": "assistant", "content": "second"},
        ]
        await persistence.run_store.delete_thread(thread_id)


async def test_postgres_run_store_rolls_back_incomplete_trace(postgres_settings) -> None:
    thread_id = f"rollback-thread-{uuid4()}"
    run_id = f"rollback-run-{uuid4()}"
    async with PersistenceFactory(postgres_settings).open() as persistence:
        event_service = AgentEventService(postgres_settings)
        response = AgentRunResponse(
            run_id=run_id,
            thread_id=thread_id,
            status="failed",
            answer="failed",
            termination_reason="failed",
        )
        event = event_service.create(
            run_id=run_id,
            thread_id=thread_id,
            event_type="run_failed",
            data={"status": "failed"},
        )

        with pytest.raises(UniqueViolation):
            await persistence.run_store.save(
                "question",
                response,
                [event, event],
                AgentRunMetrics(duration_ms=1.0, failed=True),
            )

        assert await persistence.run_store.get_run(run_id) is None


async def test_postgres_startup_rejects_missing_schema(postgres_settings) -> None:
    settings = postgres_settings.model_copy(
        update={
            "database_url": postgres_settings.database_url
            + "?options=-csearch_path%3Dmissing_persistence_schema"
        }
    )

    with pytest.raises(RuntimeError, match="checkpoint schema is not initialized"):
        async with PersistenceFactory(settings).open():
            pytest.fail("missing schema must not be accepted")


async def test_sqlite_public_history_migrates_to_postgres(
    postgres_settings, tmp_path: Path
) -> None:
    thread_id = f"migration-thread-{uuid4()}"
    run_id = f"migration-run-{uuid4()}"
    source = SqliteRunStore(str(tmp_path / "migration.sqlite3"))
    await source.setup()
    response = AgentRunResponse(
        run_id=run_id,
        thread_id=thread_id,
        status="completed",
        answer="migrated",
        termination_reason="completed",
    )
    event = AgentEventService(postgres_settings).create(
        run_id=run_id,
        thread_id=thread_id,
        event_type="run_completed",
        data={"status": "completed"},
    )
    await source.save(
        "migration question",
        response,
        [event],
        AgentRunMetrics(duration_ms=3.0),
    )

    async with PersistenceFactory(postgres_settings).open() as persistence:
        summary = await migrate_sqlite_run_store(
            tmp_path / "migration.sqlite3", persistence.run_store
        )
        trace = await persistence.run_store.get_trace(run_id)

        assert summary.runs == 1
        assert summary.events == 1
        assert trace is not None
        assert trace.run == response
        assert trace.events == [event]
        await persistence.run_store.delete_thread(thread_id)
