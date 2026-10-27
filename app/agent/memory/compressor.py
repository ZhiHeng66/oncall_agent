"""Bounded LLM summary generation followed by deterministic memory compaction."""

import asyncio
from collections.abc import Awaitable, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate

from app.agent.memory.contracts import WorkingMemoryContext, WorkingMemoryUpdate
from app.agent.specialists.data.utils import sanitize_tool_output
from app.config import Settings
from app.core.llm_factory import llm_factory

COMPRESS_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """你只能为案件记忆生成简短摘要候选。不得创造事实、来源、时间、实体或结论，
不得修改验证等级。只填写 summary_candidate；其余字段保持为空。""",
        ),
        (
            "human",
            """已有摘要：{summary}
当前目标：{goal}
有效条目（带来源引用）：{items}
待确认项：{questions}
下一步：{steps}""",
        ),
    ]
)


class WorkingMemoryCompressor:
    """Use the shared model factory only after a deterministic threshold check."""

    def __init__(
        self,
        settings: Settings,
        model: BaseChatModel | None = None,
        invoke: Callable[[WorkingMemoryContext], Awaitable[WorkingMemoryUpdate]] | None = None,
    ) -> None:
        self._settings = settings
        self._model = model
        self._invoke = invoke

    async def maybe_compress(self, context: WorkingMemoryContext) -> WorkingMemoryContext:
        if self.estimated_chars(context) < self._settings.working_memory_compression_threshold:
            return context
        try:
            update = await asyncio.wait_for(
                self._request_update(context),
                timeout=self._settings.working_memory_compression_timeout_seconds,
            )
        except Exception:
            return context
        summary = (update.summary_candidate or "").strip()
        if not summary:
            return context
        summary = sanitize_tool_output(summary, self._settings.working_memory_summary_max_chars)
        compacted = context.model_copy(update={"compressed_summary": summary})
        return self._compact_deterministically(compacted)

    async def _request_update(self, context: WorkingMemoryContext) -> WorkingMemoryUpdate:
        if self._invoke is not None:
            return WorkingMemoryUpdate.model_validate(await self._invoke(context))
        model = self._model or llm_factory.create_chat_model(role="memory")
        chain = COMPRESS_PROMPT | model.with_structured_output(WorkingMemoryUpdate)
        response = await chain.ainvoke(
            {
                "summary": context.compressed_summary or "无",
                "goal": context.current_goal or "无",
                "items": self._format_items(context),
                "questions": "；".join(context.open_questions) or "无",
                "steps": "；".join(context.next_steps) or "无",
            }
        )
        return (
            response
            if isinstance(response, WorkingMemoryUpdate)
            else WorkingMemoryUpdate.model_validate(response)
        )

    def _compact_deterministically(self, context: WorkingMemoryContext) -> WorkingMemoryContext:
        budget = self._settings.working_memory_context_max_chars
        if self.estimated_chars(context) <= budget:
            return context
        # Never remove supported observations; first remove stale historical and unverified claims.
        removable = [
            item
            for item in context.items
            if item.verification == "unverified" or item.status != "active"
        ]
        retained = list(context.items)
        for item in removable:
            if self.estimated_chars(context.model_copy(update={"items": retained})) <= budget:
                break
            retained.remove(item)
        candidate = context.model_copy(update={"items": retained})
        return candidate if self.estimated_chars(candidate) <= budget else context

    @staticmethod
    def estimated_chars(context: WorkingMemoryContext) -> int:
        return len(context.model_dump_json())

    @staticmethod
    def _format_items(context: WorkingMemoryContext) -> str:
        return "\n".join(
            f"- {item.kind}/{item.verification}/{item.status}: {item.statement}; "
            f"sources={','.join(ref.source_name for ref in item.evidence_refs) or 'none'}"
            for item in context.items
        )
