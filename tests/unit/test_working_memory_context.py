from datetime import UTC, datetime, timedelta

from app.agent.memory.context import MemoryContextBuilder, MemoryContextRenderer
from app.agent.memory.contracts import MemoryItem, PublicConversationTurn, WorkingMemoryContext
from app.config import Settings


def item(item_id: str, *, valid_until: datetime | None = None) -> MemoryItem:
    now = datetime(2026, 9, 13, tzinfo=UTC)
    return MemoryItem(
        item_id=item_id,
        kind="user_claim",
        statement=f"statement-{item_id}",
        verification="unverified",
        temporal_scope="snapshot" if valid_until else "static",
        observed_at=now if valid_until else None,
        valid_until=valid_until,
        created_run_id="run",
        updated_run_id="run",
        created_at=now,
        updated_at=now,
    )


def test_builder_separates_expired_dynamic_items_and_bounds_recent_turns() -> None:
    now = datetime(2026, 9, 13, 12, tzinfo=UTC)
    settings = Settings(
        _env_file=None, dashscope_api_key="", working_memory_recent_message_max_chars=100
    )
    memory = WorkingMemoryContext(
        thread_id="thread",
        owner_user_id="user",
        version=2,
        current_goal="diagnose",
        items=[item("static"), item("stale", valid_until=now - timedelta(seconds=1))],
        created_at=now,
        updated_at=now,
    )

    view, usage = MemoryContextBuilder(settings, now=now).build(
        memory, [PublicConversationTurn(role="user", content="123456")]
    )

    assert [value.item_id for value in view.active_items] == ["static"]
    assert [value.item_id for value in view.stale_items] == ["stale"]
    assert usage.status == "loaded"
    assert usage.stale_item_ids == ["stale"]


def test_renderer_does_not_send_expired_dynamic_statement_to_data_agent() -> None:
    now = datetime(2026, 9, 13, 12, tzinfo=UTC)
    settings = Settings(_env_file=None, dashscope_api_key="")
    memory = WorkingMemoryContext(
        thread_id="thread",
        owner_user_id="user",
        version=1,
        items=[item("old-alert", valid_until=now - timedelta(seconds=1))],
        created_at=now,
        updated_at=now,
    )
    view, _ = MemoryContextBuilder(settings, now=now).build(memory, [])
    rendered = MemoryContextRenderer(settings).for_specialist("data", view)

    assert "old-alert" in rendered
    assert "statement-old-alert" not in rendered
    assert "必须重新查询" in rendered
