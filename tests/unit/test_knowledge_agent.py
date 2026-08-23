from langchain_core.documents import Document

from app.agent.contracts import AgentTask, Evidence
from app.agent.specialists.knowledge.agent import KnowledgeAgent
from app.agent.specialists.knowledge.schemas import KnowledgeRecord
from app.config import Settings


class FakeRetriever:
    def __init__(
        self,
        records: list[KnowledgeRecord] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.records = records or []
        self.error = error

    async def retrieve(self, query: str) -> list[KnowledgeRecord]:
        if self.error:
            raise self.error
        return self.records


class FakeAnswerer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def answer(self, task: AgentTask, evidence: list[Evidence]) -> str:
        if self.error:
            raise self.error
        return "根据文档，应先收集诊断信息。[来源1]"


def make_task() -> AgentTask:
    return AgentTask(task_id="knowledge-1", objective="如何恢复服务")


async def test_knowledge_agent_returns_cited_evidence_with_source_metadata() -> None:
    record = KnowledgeRecord(
        Document(
            page_content="恢复前先收集诊断信息。",
            metadata={"_source": "docs/runbook.md", "_file_name": "runbook.md", "h1": "恢复"},
        ),
        relevance_score=0.9,
    )
    agent = KnowledgeAgent(
        retriever=FakeRetriever([record]),
        answerer=FakeAnswerer(),
        settings=Settings(_env_file=None, dashscope_api_key=""),
    )

    result = await agent.run(make_task())

    assert result.status == "success"
    assert "[来源1]" in result.summary
    assert result.evidence[0].source_name == "runbook.md"
    assert result.evidence[0].metadata["_source"] == "docs/runbook.md"
    assert result.evidence[0].metadata["relevance_score"] == 0.9


async def test_knowledge_agent_distinguishes_no_evidence_from_retrieval_failure() -> None:
    settings = Settings(_env_file=None, dashscope_api_key="")
    empty_agent = KnowledgeAgent(
        retriever=FakeRetriever(), answerer=FakeAnswerer(), settings=settings
    )
    failed_agent = KnowledgeAgent(
        retriever=FakeRetriever(error=ConnectionError("Milvus unavailable")),
        answerer=FakeAnswerer(),
        settings=settings,
    )

    empty = await empty_agent.run(make_task())
    failed = await failed_agent.run(make_task())

    assert empty.status == "partial"
    assert empty.errors == []
    assert failed.status == "failed"
    assert failed.errors == ["Milvus unavailable"]


async def test_knowledge_agent_filters_low_relevance_and_preserves_evidence_on_answer_failure() -> (
    None
):
    settings = Settings(
        _env_file=None,
        dashscope_api_key="",
        knowledge_min_relevance_score=0.5,
    )
    low = KnowledgeRecord(Document(page_content="weak"), relevance_score=0.1)
    low_result = await KnowledgeAgent(
        retriever=FakeRetriever([low]),
        answerer=FakeAnswerer(),
        settings=settings,
    ).run(make_task())

    good = KnowledgeRecord(Document(page_content="strong"), relevance_score=0.9)
    generation_failed = await KnowledgeAgent(
        retriever=FakeRetriever([good]),
        answerer=FakeAnswerer(error=RuntimeError("model unavailable")),
        settings=settings,
    ).run(make_task())

    assert low_result.status == "partial"
    assert low_result.evidence == []
    assert generation_failed.status == "partial"
    assert len(generation_failed.evidence) == 1
    assert generation_failed.errors == ["model unavailable"]
