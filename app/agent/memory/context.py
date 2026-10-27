"""Deterministic construction and role-specific rendering of working-memory context."""

from datetime import UTC, datetime

from app.agent.contracts import AgentTarget
from app.agent.memory.contracts import (
    MemoryContextView,
    MemoryItem,
    MemoryUsage,
    PublicConversationTurn,
    WorkingMemoryContext,
)
from app.config import Settings


def is_stale(item: MemoryItem, now: datetime) -> bool:
    """Dynamic observations are unusable as current facts after their server TTL."""

    return item.temporal_scope != "static" and (item.valid_until is None or item.valid_until <= now)


class MemoryContextBuilder:
    def __init__(self, settings: Settings, now: datetime | None = None) -> None:
        self._settings = settings
        self._now = now

    def build(
        self,
        memory: WorkingMemoryContext | None,
        recent_turns: list[PublicConversationTurn],
    ) -> tuple[MemoryContextView, MemoryUsage]:
        bounded_turns = [
            turn.model_copy(
                update={
                    "content": turn.content[
                        : self._settings.working_memory_recent_message_max_chars
                    ]
                }
            )
            for turn in recent_turns[-self._settings.working_memory_recent_turns :]
        ]
        if memory is None:
            return MemoryContextView(recent_turns=bounded_turns), MemoryUsage(
                status="missing", used=bool(bounded_turns)
            )
        now = self._now or datetime.now(UTC)
        active = [item for item in memory.items if item.status == "active"]
        stale = [item for item in active if is_stale(item, now)]
        usable = [item for item in active if item not in stale]
        selected = usable[: self._settings.working_memory_max_items]
        view = MemoryContextView(
            current_goal=memory.current_goal,
            entities=memory.entities,
            active_items=selected,
            stale_items=stale[: self._settings.working_memory_max_items],
            open_questions=memory.open_questions[
                : self._settings.working_memory_max_open_questions
            ],
            next_steps=memory.next_steps[: self._settings.working_memory_max_next_steps],
            recent_turns=bounded_turns,
            compressed_summary=memory.compressed_summary,
        )
        truncated = (
            len(usable) > len(selected) or len(stale) > self._settings.working_memory_max_items
        )
        usage = MemoryUsage(
            status="loaded",
            version=memory.version,
            selected_item_ids=[item.item_id for item in selected],
            stale_item_ids=[item.item_id for item in stale],
            used=bool(selected or stale or view.recent_turns or memory.current_goal),
            truncated=truncated,
        )
        return view, usage


class MemoryContextRenderer:
    """Render only the minimum role-specific context within a deterministic char limit."""

    def __init__(self, settings: Settings) -> None:
        self._limit = settings.working_memory_context_max_chars

    def for_supervisor(self, view: MemoryContextView) -> str:
        return self._bounded(self._common(view, include_dynamic=True) + self._recent_turns(view))

    def for_specialist(self, target: AgentTarget, view: MemoryContextView) -> str:
        if target == "data":
            return self._bounded(
                self._goal_entities(view)
                + self._lines("待重新查询的动态观察", [item.item_id for item in view.stale_items])
                + (
                    "动态观察已过期，必须重新查询；不要将旧观察描述为当前事实。"
                    if view.stale_items
                    else ""
                )
                + self._recent_turns(view)
            )
        if target == "knowledge":
            static_items = [
                item.statement for item in view.active_items if item.temporal_scope == "static"
            ]
            return self._bounded(
                self._goal_entities(view)
                + self._lines("可复用静态事实", static_items)
                + self._recent_turns(view)
            )
        return self._bounded(self._common(view, include_dynamic=True) + self._recent_turns(view))

    def for_reviewer(self, view: MemoryContextView) -> str:
        return self._bounded(
            self._goal_entities(view)
            + self._lines("仍待确认", view.open_questions)
            + self._lines(
                "过期动态观察（不可当作当前事实）", [item.statement for item in view.stale_items]
            )
        )

    def _common(self, view: MemoryContextView, *, include_dynamic: bool) -> str:
        items = [
            item.statement
            for item in view.active_items
            if include_dynamic or item.temporal_scope == "static"
        ]
        return (
            self._goal_entities(view)
            + self._lines("有效案件事实", items)
            + self._lines("下一步", view.next_steps)
            + self._lines("仍待确认", view.open_questions)
        )

    @staticmethod
    def _goal_entities(view: MemoryContextView) -> str:
        entities = [f"{key}: {value}" for key, value in view.entities.items()]
        return MemoryContextRenderer._lines(
            "当前目标", [view.current_goal]
        ) + MemoryContextRenderer._lines("关键实体", entities)

    @staticmethod
    def _lines(title: str, values: list[str]) -> str:
        values = [value.strip() for value in values if value.strip()]
        return f"{title}：\n" + "\n".join(f"- {value}" for value in values) + "\n" if values else ""

    @staticmethod
    def _recent_turns(view: MemoryContextView) -> str:
        return MemoryContextRenderer._lines(
            "最近公开对话",
            [f"{turn.role}: {turn.content}" for turn in view.recent_turns],
        )

    def _bounded(self, value: str) -> str:
        if len(value) <= self._limit:
            return value
        return value[: self._limit - 1].rstrip() + "…"
