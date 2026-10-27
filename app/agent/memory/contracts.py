"""Typed, bounded contracts for PostgreSQL-backed working memory."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.agent.contracts import EvidenceSourceType, TemporalScope

MemoryItemKind = Literal[
    "user_claim",
    "evidence_observation",
    "conclusion",
    "decision",
]
MemoryItemStatus = Literal["active", "superseded", "resolved"]
VerificationStatus = Literal["unverified", "supported"]


def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("memory timestamps must include a timezone")
    return value


class MemoryEvidenceRef(BaseModel):
    """A safe, bounded pointer to evidence retained in a completed run."""

    run_id: str = Field(min_length=1, max_length=128)
    agent_name: str = Field(min_length=1, max_length=64)
    source_type: EvidenceSourceType
    source_name: str = Field(min_length=1, max_length=256)
    source_id: str | None = Field(default=None, max_length=256)
    excerpt: str = Field(min_length=1, max_length=1000)
    observed_at: datetime | None = None

    _validate_observed_at = field_validator("observed_at")(_require_aware)


class MemoryItem(BaseModel):
    """One traceable statement in the active or historical case context."""

    item_id: str = Field(min_length=1, max_length=128)
    kind: MemoryItemKind
    statement: str = Field(min_length=1, max_length=2000)
    status: MemoryItemStatus = "active"
    verification: VerificationStatus
    evidence_refs: list[MemoryEvidenceRef] = Field(default_factory=list, max_length=16)
    temporal_scope: TemporalScope = "static"
    observed_at: datetime | None = None
    valid_until: datetime | None = None
    query_scope: str | None = Field(default=None, max_length=128)
    created_run_id: str = Field(min_length=1, max_length=128)
    updated_run_id: str = Field(min_length=1, max_length=128)
    created_at: datetime
    updated_at: datetime

    _validate_timestamps = field_validator(
        "observed_at",
        "valid_until",
        "created_at",
        "updated_at",
    )(_require_aware)

    @model_validator(mode="after")
    def validate_provenance_and_time(self) -> "MemoryItem":
        if self.verification == "supported" and not self.evidence_refs:
            raise ValueError("supported memory item requires evidence_refs")
        if self.temporal_scope != "static" and self.observed_at is None:
            raise ValueError("dynamic memory item requires observed_at")
        if (
            self.observed_at is not None
            and self.valid_until is not None
            and self.valid_until <= self.observed_at
        ):
            raise ValueError("valid_until must be later than observed_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self


class WorkingMemoryContext(BaseModel):
    """Materialized, bounded case context loaded by business thread."""

    schema_version: int = Field(default=1, ge=1)
    thread_id: str = Field(min_length=1, max_length=128)
    owner_user_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    current_goal: str = Field(default="", max_length=2000)
    entities: dict[str, str] = Field(default_factory=dict, max_length=32)
    items: list[MemoryItem] = Field(default_factory=list, max_length=100)
    open_questions: list[str] = Field(default_factory=list, max_length=32)
    next_steps: list[str] = Field(default_factory=list, max_length=32)
    compressed_summary: str = Field(default="", max_length=4000)
    last_run_id: str | None = Field(default=None, max_length=128)
    created_at: datetime
    updated_at: datetime

    _validate_timestamps = field_validator("created_at", "updated_at")(_require_aware)

    @field_validator("entities")
    @classmethod
    def validate_entities(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or len(key) > 128 for key in value):
            raise ValueError("entity keys must be non-blank and at most 128 characters")
        if any(not item.strip() or len(item) > 500 for item in value.values()):
            raise ValueError("entity values must be non-blank and at most 500 characters")
        return value

    @field_validator("open_questions", "next_steps")
    @classmethod
    def validate_short_lists(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 1000 for item in value):
            raise ValueError("memory list entries must be non-blank and at most 1000 characters")
        return value

    @model_validator(mode="after")
    def validate_update_time(self) -> "WorkingMemoryContext":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self


class PublicConversationTurn(BaseModel):
    """A bounded, public turn recovered from completed runs."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class MemoryUsage(BaseModel):
    """Non-sensitive diagnostics for one request's memory preparation."""

    status: Literal["disabled", "missing", "loaded", "failed"] = "disabled"
    version: int | None = Field(default=None, ge=1)
    selected_item_ids: list[str] = Field(default_factory=list, max_length=32)
    stale_item_ids: list[str] = Field(default_factory=list, max_length=32)
    used: bool = False
    truncated: bool = False


class MemoryContextView(BaseModel):
    """Ephemeral, role-filtered source material; it is never persisted as a prompt."""

    current_goal: str = ""
    entities: dict[str, str] = Field(default_factory=dict)
    active_items: list[MemoryItem] = Field(default_factory=list)
    stale_items: list[MemoryItem] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    recent_turns: list[PublicConversationTurn] = Field(default_factory=list)
    compressed_summary: str = ""


class MemoryItemProposal(BaseModel):
    """A model-proposed item whose server-owned fields are assigned by the reducer."""

    kind: MemoryItemKind
    statement: str = Field(min_length=1, max_length=2000)
    verification: VerificationStatus
    evidence_refs: list[MemoryEvidenceRef] = Field(default_factory=list, max_length=16)
    temporal_scope: TemporalScope = "static"
    query_scope: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_supported_proposal(self) -> "MemoryItemProposal":
        if self.verification == "supported" and not self.evidence_refs:
            raise ValueError("supported memory proposal requires evidence_refs")
        return self


class WorkingMemoryUpdate(BaseModel):
    """A bounded delta; models never replace a complete memory snapshot."""

    goal_update: str | None = Field(default=None, max_length=2000)
    entity_updates: dict[str, str] = Field(default_factory=dict, max_length=32)
    additions: list[MemoryItemProposal] = Field(default_factory=list, max_length=32)
    resolve_item_ids: list[str] = Field(default_factory=list, max_length=32)
    supersede_item_ids: list[str] = Field(default_factory=list, max_length=32)
    open_questions: list[str] = Field(default_factory=list, max_length=32)
    next_steps: list[str] = Field(default_factory=list, max_length=32)
    summary_candidate: str | None = Field(default=None, max_length=4000)

    @field_validator("entity_updates")
    @classmethod
    def validate_entities(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or len(key) > 128 for key in value):
            raise ValueError("entity keys must be non-blank and at most 128 characters")
        if any(not item.strip() or len(item) > 500 for item in value.values()):
            raise ValueError("entity values must be non-blank and at most 500 characters")
        return value

    @field_validator("resolve_item_ids", "supersede_item_ids")
    @classmethod
    def validate_item_ids(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 128 for item in value):
            raise ValueError("memory item ids must be non-blank and at most 128 characters")
        return value

    @field_validator("open_questions", "next_steps")
    @classmethod
    def validate_short_lists(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 1000 for item in value):
            raise ValueError("memory list entries must be non-blank and at most 1000 characters")
        return value
