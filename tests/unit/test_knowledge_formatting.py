from importlib import import_module
from typing import Any

from langchain_core.documents import Document

from app.tools.knowledge_tool import format_docs

knowledge_tool = import_module("app.tools.knowledge_tool")


def test_format_docs_numbers_sources_and_preserves_headers() -> None:
    docs = [
        Document(
            page_content="Reset the service only after collecting diagnostics.",
            metadata={"_file_name": "runbook.md", "h1": "Operations", "h2": "Recovery"},
        ),
        Document(
            page_content="Escalate unresolved incidents.",
            metadata={"_file_name": "policy.md"},
        ),
    ]

    formatted = format_docs(docs)

    assert "【参考资料 1】" in formatted
    assert "标题: Operations > Recovery" in formatted
    assert "来源: runbook.md" in formatted
    assert "【参考资料 2】" in formatted
    assert "来源: policy.md" in formatted
    assert docs[0].page_content in formatted


def test_retrieve_knowledge_uses_injected_fake_retriever(monkeypatch: Any) -> None:
    docs = [
        Document(
            page_content="Evidence from a fake retriever.",
            metadata={"_file_name": "fake.md"},
        )
    ]

    class FakeRetriever:
        def invoke(self, query: str) -> list[Document]:
            assert query == "test query"
            return docs

    class FakeVectorStore:
        def as_retriever(self, *, search_kwargs: dict[str, int]) -> FakeRetriever:
            assert search_kwargs["k"] > 0
            return FakeRetriever()

    class FakeVectorStoreManager:
        def get_vector_store(self) -> FakeVectorStore:
            return FakeVectorStore()

    monkeypatch.setattr(
        knowledge_tool,
        "vector_store_manager",
        FakeVectorStoreManager(),
    )

    content, artifacts = knowledge_tool.retrieve_knowledge.func("test query")

    assert "来源: fake.md" in content
    assert artifacts == docs
