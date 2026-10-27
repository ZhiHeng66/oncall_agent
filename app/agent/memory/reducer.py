"""Deterministic, evidence-preserving working-memory update and merge logic."""

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from app.agent.contracts import Evidence, MultiAgentRunResult
from app.agent.memory.contracts import (
    MemoryEvidenceRef,
    MemoryItem,
    MemoryItemProposal,
    WorkingMemoryContext,
    WorkingMemoryUpdate,
)
from app.agent.specialists.data.utils import sanitize_tool_output
from app.config import Settings


@dataclass(frozen=True)
class PreparedAddition:
    proposal: MemoryItemProposal
    observed_at: datetime | None = None
    valid_until: datetime | None = None


class WorkingMemoryReducer:
    """Turn public request/result material into a bounded, traceable snapshot."""

    def __init__(self, settings: Settings, now: datetime | None = None) -> None:
        self._settings = settings
        self._now = now or datetime.now(UTC)

    def build_update(
        self,
        request_message: str,
        result: MultiAgentRunResult,
    ) -> tuple[WorkingMemoryUpdate, list[PreparedAddition]]:
        additions = [
            PreparedAddition(
                MemoryItemProposal(
                    kind="user_claim",
                    statement=sanitize_tool_output(request_message, 2000),
                    verification="unverified",
                )
            )
        ]
        open_questions: list[str] = []
        for agent_result in result.agent_results:
            for evidence in agent_result.evidence:
                additions.append(
                    self._evidence_addition(result.run_id, agent_result.agent_name, evidence)
                )
            if agent_result.errors:
                open_questions.append(
                    f"待确认 {agent_result.agent_name} 执行未完成："
                    f"{sanitize_tool_output('；'.join(agent_result.errors), 800)}"
                )
        additions = additions[: self._settings.working_memory_max_items]
        update = WorkingMemoryUpdate(
            goal_update=sanitize_tool_output(request_message, 2000),
            additions=[item.proposal for item in additions],
            open_questions=open_questions[: self._settings.working_memory_max_open_questions],
            next_steps=(["重新查询或处理仍待确认的执行项。"] if open_questions else []),
        )
        return update, additions

    def apply(
        self,
        existing: WorkingMemoryContext | None,
        *,
        thread_id: str,
        owner_user_id: str,
        run_id: str,
        update: WorkingMemoryUpdate,
        additions: list[PreparedAddition],
    ) -> WorkingMemoryContext | None:
        if existing is not None and existing.last_run_id == run_id:
            return None
        base = existing or WorkingMemoryContext(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            version=1,
            created_at=self._now,
            updated_at=self._now,
        )
        if base.thread_id != thread_id or base.owner_user_id != owner_user_id:
            raise ValueError("working-memory owner or thread mismatch")
        items = list(base.items)
        changed = False
        for addition in additions:
            proposal = addition.proposal
            if self._has_active_duplicate(items, proposal):
                continue
            if proposal.kind == "evidence_observation" and proposal.query_scope:
                for index, item in enumerate(items):
                    if (
                        item.status == "active"
                        and item.kind == "evidence_observation"
                        and item.query_scope == proposal.query_scope
                    ):
                        items[index] = item.model_copy(
                            update={
                                "status": "superseded",
                                "updated_run_id": run_id,
                                "updated_at": self._now,
                            }
                        )
                        changed = True
            items.append(self._materialize(proposal, run_id, addition))
            changed = True
        questions = self._merge_strings(
            base.open_questions,
            update.open_questions,
            self._settings.working_memory_max_open_questions,
        )
        steps = self._merge_strings(
            base.next_steps, update.next_steps, self._settings.working_memory_max_next_steps
        )
        goal = update.goal_update or base.current_goal
        changed = (
            changed
            or goal != base.current_goal
            or questions != base.open_questions
            or steps != base.next_steps
        )
        if not changed:
            return None
        items = self._trim_items(items)
        return WorkingMemoryContext(
            schema_version=base.schema_version,
            thread_id=thread_id,
            owner_user_id=owner_user_id,
            version=1 if existing is None else base.version + 1,
            current_goal=goal,
            entities=base.entities,
            items=items,
            open_questions=questions,
            next_steps=steps,
            compressed_summary=base.compressed_summary,
            last_run_id=run_id,
            created_at=base.created_at,
            updated_at=self._now,
        )

    def _evidence_addition(
        self, run_id: str, agent_name: str, evidence: Evidence
    ) -> PreparedAddition:
        excerpt = sanitize_tool_output(evidence.content, 1000)
        reference = MemoryEvidenceRef(
            run_id=run_id,
            agent_name=agent_name,
            source_type=evidence.source_type,
            source_name=evidence.source_name,
            source_id=evidence.source_id,
            excerpt=excerpt,
            observed_at=evidence.observed_at,
        )
        return PreparedAddition(
            MemoryItemProposal(
                kind="evidence_observation",
                statement=excerpt,
                verification="supported",
                evidence_refs=[reference],
                temporal_scope=evidence.temporal_scope,
                query_scope=evidence.query_scope,
            ),
            observed_at=evidence.observed_at,
            valid_until=evidence.valid_until,
        )

    def _materialize(
        self, proposal: MemoryItemProposal, run_id: str, addition: PreparedAddition
    ) -> MemoryItem:
        item_hash = sha256(f"{run_id}:{proposal.kind}:{proposal.statement}".encode()).hexdigest()[
            :24
        ]
        return MemoryItem(
            item_id=f"mem-{item_hash}",
            kind=proposal.kind,
            statement=proposal.statement,
            verification=proposal.verification,
            evidence_refs=proposal.evidence_refs,
            temporal_scope=proposal.temporal_scope,
            observed_at=addition.observed_at,
            valid_until=addition.valid_until,
            query_scope=proposal.query_scope,
            created_run_id=run_id,
            updated_run_id=run_id,
            created_at=self._now,
            updated_at=self._now,
        )

    @staticmethod
    def _has_active_duplicate(items: list[MemoryItem], proposal: MemoryItemProposal) -> bool:
        normalized = proposal.statement.strip().casefold()
        return any(
            item.status == "active"
            and item.kind == proposal.kind
            and item.statement.casefold() == normalized
            for item in items
        )

    @staticmethod
    def _merge_strings(existing: list[str], new: list[str], maximum: int) -> list[str]:
        return list(dict.fromkeys([*existing, *new]))[-maximum:]

    def _trim_items(self, items: list[MemoryItem]) -> list[MemoryItem]:
        if len(items) <= self._settings.working_memory_max_items:
            return items
        retained = sorted(
            items,
            key=lambda item: (item.status != "active", item.updated_at),
            reverse=False,
        )[: self._settings.working_memory_max_items]
        return sorted(retained, key=lambda item: item.created_at)
