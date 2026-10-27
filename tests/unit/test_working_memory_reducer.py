from datetime import UTC, datetime, timedelta

from app.agent.contracts import AgentResult, Evidence, MultiAgentRunResult
from app.agent.memory.reducer import WorkingMemoryReducer
from app.config import Settings


def result(run_id: str, evidence: list[Evidence] | None = None, errors: list[str] | None = None):
    return MultiAgentRunResult(
        run_id=run_id,
        thread_id="thread",
        status="completed",
        final_answer="answer",
        agent_results=[
            AgentResult(
                agent_name="data",
                task_id="task",
                status="success" if not errors else "failed",
                summary="summary",
                evidence=evidence or [],
                errors=errors or [],
            )
        ],
        termination_reason="done",
    )


def test_reducer_creates_traceable_items_and_deduplicates_same_claim() -> None:
    settings = Settings(_env_file=None, dashscope_api_key="")
    reducer = WorkingMemoryReducer(settings, now=datetime(2026, 9, 13, tzinfo=UTC))
    first_result = result(
        "run-1", [Evidence(source_type="knowledge", source_name="guide", content="fact")]
    )
    update, additions = reducer.build_update("investigate", first_result)
    first = reducer.apply(
        None,
        thread_id="thread",
        owner_user_id="user",
        run_id="run-1",
        update=update,
        additions=additions,
    )

    assert first is not None
    assert first.version == 1
    assert {item.kind for item in first.items} == {"user_claim", "evidence_observation"}
    assert first.items[1].evidence_refs[0].run_id == "run-1"

    repeat_update, repeat_additions = reducer.build_update("investigate", result("run-2"))
    second = reducer.apply(
        first,
        thread_id="thread",
        owner_user_id="user",
        run_id="run-2",
        update=repeat_update,
        additions=repeat_additions,
    )
    assert second is None


def test_new_dynamic_observation_supersedes_same_query_scope() -> None:
    settings = Settings(_env_file=None, dashscope_api_key="")
    now = datetime(2026, 9, 13, tzinfo=UTC)
    reducer = WorkingMemoryReducer(settings, now=now)
    old_evidence = Evidence(
        source_type="tool",
        source_name="alerts",
        content="old alert",
        temporal_scope="snapshot",
        observed_at=now,
        valid_until=now + timedelta(minutes=1),
        query_scope="scope",
    )
    first_update, first_additions = reducer.build_update("check", result("run-1", [old_evidence]))
    first = reducer.apply(
        None,
        thread_id="thread",
        owner_user_id="user",
        run_id="run-1",
        update=first_update,
        additions=first_additions,
    )
    assert first is not None
    new_evidence = old_evidence.model_copy(update={"content": "new alert"})
    second_update, second_additions = reducer.build_update(
        "check again", result("run-2", [new_evidence])
    )
    second = reducer.apply(
        first,
        thread_id="thread",
        owner_user_id="user",
        run_id="run-2",
        update=second_update,
        additions=second_additions,
    )

    assert second is not None
    observations = [item for item in second.items if item.kind == "evidence_observation"]
    assert [item.status for item in observations] == ["superseded", "active"]


def test_failed_tool_only_creates_unverified_claim_and_open_question() -> None:
    settings = Settings(_env_file=None, dashscope_api_key="")
    reducer = WorkingMemoryReducer(settings, now=datetime(2026, 9, 13, tzinfo=UTC))
    update, additions = reducer.build_update(
        "check alerts", result("run", errors=["tool unavailable"])
    )
    memory = reducer.apply(
        None,
        thread_id="thread",
        owner_user_id="user",
        run_id="run",
        update=update,
        additions=additions,
    )

    assert memory is not None
    assert [item.kind for item in memory.items] == ["user_claim"]
    assert memory.items[0].verification == "unverified"
    assert "待确认" in memory.open_questions[0]
