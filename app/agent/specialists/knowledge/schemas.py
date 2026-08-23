"""Knowledge specialist schemas."""

from dataclasses import dataclass

from langchain_core.documents import Document
from pydantic import BaseModel, Field


@dataclass(frozen=True, slots=True)
class KnowledgeRecord:
    document: Document
    relevance_score: float | None = None


class KnowledgeAnswer(BaseModel):
    answer: str = Field(description="仅依据证据形成、带 [来源N] 引用的回答")
