from langchain_core.documents import Document

from app.agent.specialists.knowledge.retriever import MilvusKnowledgeRetriever
from app.config import Settings


class FakeVectorStore:
    def similarity_search_with_relevance_scores(
        self, query: str, *, k: int
    ) -> list[tuple[Document, float]]:
        assert query == "release policy"
        assert k == 2
        return [
            (
                Document(
                    page_content="Release evidence",
                    metadata={"_file_name": "release.md"},
                ),
                0.85,
            )
        ]


class FakeStoreManager:
    def get_vector_store(self) -> FakeVectorStore:
        return FakeVectorStore()


async def test_milvus_retriever_preserves_relevance_score_without_network() -> None:
    retriever = MilvusKnowledgeRetriever(
        store_manager=FakeStoreManager(),  # type: ignore[arg-type]
        settings=Settings(_env_file=None, dashscope_api_key="", rag_top_k=2),
    )

    records = await retriever.retrieve("release policy")

    assert records[0].document.metadata["_file_name"] == "release.md"
    assert records[0].relevance_score == 0.85
