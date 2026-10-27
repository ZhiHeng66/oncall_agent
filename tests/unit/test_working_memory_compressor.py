from datetime import UTC, datetime

from app.agent.memory.compressor import WorkingMemoryCompressor
from app.agent.memory.contracts import (
    MemoryEvidenceRef,
    MemoryItem,
    WorkingMemoryContext,
    WorkingMemoryUpdate,
)
from app.config import Settings


def context(statement: str = "x" * 400) -> WorkingMemoryContext:
    now = datetime(2026, 9, 13, tzinfo=UTC)
    return WorkingMemoryContext(
        thread_id="thread",
        owner_user_id="user",
        version=2,
        current_goal="diagnose",
        items=[
            MemoryItem(
                item_id="evidence",
                kind="evidence_observation",
                statement=statement,
                verification="supported",
                evidence_refs=[
                    MemoryEvidenceRef(
                        run_id="run",
                        agent_name="data",
                        source_type="tool",
                        source_name="monitor",
                        excerpt="safe excerpt",
                    )
                ],
                created_run_id="run",
                updated_run_id="run",
                created_at=now,
                updated_at=now,
            )
        ],
        created_at=now,
        updated_at=now,
    )


async def test_compressor_uses_structured_summary_only_after_threshold() -> None:
    called = False

    async def invoke(_: WorkingMemoryContext) -> WorkingMemoryUpdate:
        nonlocal called
        called = True
        return WorkingMemoryUpdate(
            summary_candidate="safe summary",
            entity_updates={"forbidden": "value"},
            additions=[],
        )

    settings = Settings(
        _env_file=None,
        dashscope_api_key="",
        working_memory_compression_threshold=500,
        working_memory_context_max_chars=1000,
        working_memory_summary_max_chars=1000,
    )
    original = context()
    compressed = await WorkingMemoryCompressor(settings, invoke=invoke).maybe_compress(original)

    assert called is True
    assert compressed.compressed_summary == "safe summary"
    assert compressed.entities == {}
    assert compressed.items[0].evidence_refs[0].source_name == "monitor"


async def test_compressor_does_not_call_model_below_threshold_or_on_failure() -> None:
    called = False

    async def invoke(_: WorkingMemoryContext) -> WorkingMemoryUpdate:
        nonlocal called
        called = True
        raise RuntimeError("provider unavailable")

    high_threshold = Settings(
        _env_file=None,
        dashscope_api_key="",
        working_memory_compression_threshold=1000,
        working_memory_context_max_chars=1000,
        working_memory_summary_max_chars=1000,
    )
    original = context("short")
    assert (
        await WorkingMemoryCompressor(high_threshold, invoke=invoke).maybe_compress(original)
        == original
    )
    assert called is False

    low_threshold = high_threshold.model_copy(update={"working_memory_compression_threshold": 500})
    assert (
        await WorkingMemoryCompressor(low_threshold, invoke=invoke).maybe_compress(context())
        == context()
    )
    assert called is True
