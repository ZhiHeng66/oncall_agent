"""In-process WorkingMemoryStore fake for unit and service tests."""

import asyncio

from app.agent.memory.contracts import WorkingMemoryContext


class FakeWorkingMemoryStore:
    def __init__(self) -> None:
        self._contexts: dict[str, WorkingMemoryContext] = {}
        self._lock = asyncio.Lock()

    async def setup(self) -> None:
        return None

    async def load(self, thread_id: str) -> WorkingMemoryContext | None:
        async with self._lock:
            return self._contexts.get(thread_id)

    async def create(self, context: WorkingMemoryContext) -> bool:
        context = WorkingMemoryContext.model_validate(context.model_dump(mode="python"))
        if context.version != 1:
            raise ValueError("new working-memory context must start at version 1")
        async with self._lock:
            if context.thread_id in self._contexts:
                return False
            self._contexts[context.thread_id] = context
            return True

    async def compare_and_swap(
        self,
        context: WorkingMemoryContext,
        expected_version: int,
    ) -> bool:
        context = WorkingMemoryContext.model_validate(context.model_dump(mode="python"))
        if context.version != expected_version + 1:
            raise ValueError("updated context version must equal expected_version + 1")
        async with self._lock:
            current = self._contexts.get(context.thread_id)
            if current is None or current.version != expected_version:
                return False
            self._contexts[context.thread_id] = context
            return True

    async def delete_thread(self, thread_id: str) -> None:
        async with self._lock:
            self._contexts.pop(thread_id, None)
