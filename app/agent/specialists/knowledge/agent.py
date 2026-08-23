"""Grounded Knowledge Agent backed only by the enterprise retriever."""

from typing import Protocol

from langchain_core.language_models.chat_models import BaseChatModel

from app.agent.contracts import AgentResult, AgentTask, Evidence
from app.agent.mcp_client import sanitize_error_message
from app.agent.specialists.knowledge.prompt import KNOWLEDGE_PROMPT
from app.agent.specialists.knowledge.retriever import (
    KnowledgeRetriever,
    MilvusKnowledgeRetriever,
)
from app.agent.specialists.knowledge.schemas import KnowledgeAnswer, KnowledgeRecord
from app.config import Settings, config
from app.core.llm_factory import llm_factory


class KnowledgeAnswerer(Protocol):
    async def answer(self, task: AgentTask, evidence: list[Evidence]) -> str: ...


class LLMKnowledgeAnswerer:
    def __init__(self, model: BaseChatModel | None = None) -> None:
        self._model = model or llm_factory.create_chat_model(role="specialist")

    async def answer(self, task: AgentTask, evidence: list[Evidence]) -> str:
        evidence_text = "\n\n".join(
            f"[来源{index}] {item.source_name}\n{item.content}"
            for index, item in enumerate(evidence, 1)
        )
        chain = KNOWLEDGE_PROMPT | self._model.with_structured_output(KnowledgeAnswer)
        response = await chain.ainvoke(
            {
                "objective": task.objective,
                "context": task.context or "无",
                "expected_output": task.expected_output,
                "constraints": "；".join(task.constraints) or "无",
                "evidence_text": evidence_text,
            }
        )
        answer = (
            response.answer
            if isinstance(response, KnowledgeAnswer)
            else KnowledgeAnswer.model_validate(response).answer
        )
        if not answer.strip():
            raise ValueError("知识回答生成结果为空")
        if not any(f"[来源{index}]" in answer for index in range(1, len(evidence) + 1)):
            citations = " ".join(f"[来源{index}]" for index in range(1, len(evidence) + 1))
            answer = f"{answer}\n\n依据：{citations}"
        return answer


class KnowledgeAgent:
    """Retrieve evidence and produce a grounded, cited result."""

    name = "knowledge"

    def __init__(
        self,
        retriever: KnowledgeRetriever | None = None,
        answerer: KnowledgeAnswerer | None = None,
        settings: Settings = config,
    ) -> None:
        self._retriever = retriever or MilvusKnowledgeRetriever(settings=settings)
        self._answerer = answerer
        self._settings = settings

    async def run(self, task: AgentTask) -> AgentResult:
        try:
            records = await self._retriever.retrieve(task.objective)
        except Exception as exc:
            detail = sanitize_error_message(str(exc))
            return AgentResult(
                agent_name=self.name,
                task_id=task.task_id,
                status="failed",
                summary="知识库检索失败，无法形成有依据的回答。",
                errors=[detail],
            )

        usable = [
            record
            for record in records
            if record.relevance_score is None
            or record.relevance_score >= self._settings.knowledge_min_relevance_score
        ]
        if not usable:
            return AgentResult(
                agent_name=self.name,
                task_id=task.task_id,
                status="partial",
                summary="知识库中没有找到足够相关的证据。",
            )

        evidence = [self._to_evidence(record, index) for index, record in enumerate(usable, 1)]
        try:
            answerer = self._answerer or LLMKnowledgeAnswerer()
            answer = await answerer.answer(task, evidence)
        except Exception as exc:
            detail = sanitize_error_message(str(exc))
            return AgentResult(
                agent_name=self.name,
                task_id=task.task_id,
                status="partial",
                summary="已检索到证据，但回答生成失败。",
                evidence=evidence,
                errors=[detail],
            )

        return AgentResult(
            agent_name=self.name,
            task_id=task.task_id,
            status="success",
            summary=answer,
            evidence=evidence,
        )

    @staticmethod
    def _to_evidence(record: KnowledgeRecord, index: int) -> Evidence:
        metadata = dict(record.document.metadata)
        source_name = str(metadata.get("_file_name") or metadata.get("_source") or "未知来源")
        source_id = str(metadata.get("chunk_id") or metadata.get("id") or f"source-{index}")
        if record.relevance_score is not None:
            metadata["relevance_score"] = record.relevance_score
        return Evidence(
            source_type="knowledge",
            source_name=source_name,
            source_id=source_id,
            content=record.document.page_content,
            metadata=metadata,
        )


knowledge_agent = KnowledgeAgent()
