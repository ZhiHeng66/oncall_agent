import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fakes.working_memory_store import FakeWorkingMemoryStore

from app.agent.memory.contracts import WorkingMemoryContext

USER_ID = UUID("00000000-0000-0000-0000-000000000011")


def context(thread_id: str, version: int = 1) -> WorkingMemoryContext:
    now = datetime.now(UTC) + timedelta(seconds=version)
    return WorkingMemoryContext(
        thread_id=thread_id,
        owner_user_id=str(USER_ID),
        version=version,
        current_goal=f"goal-{version}",
        created_at=now - timedelta(seconds=version),
        updated_at=now,
    )


async def test_fake_store_create_load_compare_and_swap_delete() -> None:
    store = FakeWorkingMemoryStore()
    first = context("thread-1")

    assert await store.create(first) is True
    assert await store.create(first) is False
    assert await store.load(USER_ID, "thread-1") == first

    second = context("thread-1", version=2)
    assert await store.compare_and_swap(second, expected_version=1) is True
    assert await store.compare_and_swap(second, expected_version=1) is False
    assert await store.load(USER_ID, "thread-1") == second

    await store.delete_thread(USER_ID, "thread-1")
    assert await store.load(USER_ID, "thread-1") is None


async def test_fake_store_allows_only_one_concurrent_version_update() -> None:
    store = FakeWorkingMemoryStore()
    assert await store.create(context("thread-1"))

    results = await asyncio.gather(
        store.compare_and_swap(context("thread-1", version=2), expected_version=1),
        store.compare_and_swap(context("thread-1", version=2), expected_version=1),
    )

    assert sum(results) == 1


async def test_fake_store_keeps_threads_isolated() -> None:
    store = FakeWorkingMemoryStore()
    first = context("thread-1")
    second = context("thread-2")

    assert await store.create(first)
    assert await store.create(second)
    assert await store.load(USER_ID, "thread-1") == first
    assert await store.load(USER_ID, "thread-2") == second
