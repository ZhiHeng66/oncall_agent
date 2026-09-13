from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.agent.contracts import Evidence
from app.agent.memory.contracts import (
    MemoryEvidenceRef,
    MemoryItem,
    WorkingMemoryContext,
)


def evidence_ref(now: datetime) -> MemoryEvidenceRef:
    return MemoryEvidenceRef(
        run_id="run-1",
        agent_name="data",
        source_type="tool",
        source_name="monitor",
        source_id="query_metrics",
        excerpt="error rate is elevated",
        observed_at=now,
    )


def test_supported_memory_item_requires_evidence_reference() -> None:
    now = datetime.now(UTC)

    with pytest.raises(ValidationError, match="requires evidence_refs"):
        MemoryItem(
            item_id="fact-1",
            kind="evidence_observation",
            statement="错误率升高",
            verification="supported",
            temporal_scope="snapshot",
            observed_at=now,
            created_run_id="run-1",
            updated_run_id="run-1",
            created_at=now,
            updated_at=now,
        )


def test_dynamic_memory_and_evidence_require_observation_time() -> None:
    with pytest.raises(ValidationError, match="dynamic evidence requires observed_at"):
        Evidence(
            source_type="tool",
            source_name="monitor",
            content="current alerts",
            temporal_scope="snapshot",
        )

    with pytest.raises(ValidationError, match="must include a timezone"):
        Evidence(
            source_type="tool",
            source_name="monitor",
            content="current alerts",
            temporal_scope="snapshot",
            observed_at=datetime.now(),
        )

    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="dynamic memory item requires observed_at"):
        MemoryItem(
            item_id="fact-1",
            kind="evidence_observation",
            statement="存在活动告警",
            verification="supported",
            evidence_refs=[evidence_ref(now)],
            temporal_scope="snapshot",
            created_run_id="run-1",
            updated_run_id="run-1",
            created_at=now,
            updated_at=now,
        )


def test_user_claim_remains_explicitly_unverified() -> None:
    now = datetime.now(UTC)
    item = MemoryItem(
        item_id="claim-1",
        kind="user_claim",
        statement="问题从上午十点开始",
        verification="unverified",
        temporal_scope="static",
        created_run_id="run-1",
        updated_run_id="run-1",
        created_at=now,
        updated_at=now,
    )

    assert item.evidence_refs == []
    assert item.verification == "unverified"


def test_context_rejects_naive_timestamps_and_invalid_order() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="must include a timezone"):
        WorkingMemoryContext(
            thread_id="thread-1",
            owner_user_id="user-1",
            version=1,
            created_at=datetime.now(),
            updated_at=now,
        )

    with pytest.raises(ValidationError, match="cannot be earlier"):
        WorkingMemoryContext(
            thread_id="thread-1",
            owner_user_id="user-1",
            version=1,
            created_at=now,
            updated_at=now - timedelta(seconds=1),
        )
