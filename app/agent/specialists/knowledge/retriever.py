"""Milvus-backed retriever boundary for the Knowledge Agent."""

import asyncio
from typing import Protocol

from app.agent.specialists.knowledge.schemas import KnowledgeRecord
from app.config import Settings, config
from app.services.vector_store_manager import VectorStoreManager, vector_store_manager


class KnowledgeRetriever(Protocol):
    async def retrieve(self, query: str) -> list[KnowledgeRecord]: ...


class MilvusKnowledgeRetriever:
    """Run the synchronous LangChain retriever outside the event loop."""

    def __init__(
        self,
        store_manager: VectorStoreManager = vector_store_manager,
        settings: Settings = config,
    ) -> None:
        self._store_manager = store_manager
        self._settings = settings

    async def retrieve(self, query: str) -> list[KnowledgeRecord]:
        def search() -> list[KnowledgeRecord]:
            vector_store = self._store_manager.get_vector_store()
            results = vector_store.similarity_search_with_relevance_scores(
                query,
                k=self._settings.rag_top_k,
            )
            return [KnowledgeRecord(document=doc, relevance_score=score) for doc, score in results]

        return await asyncio.to_thread(search)
