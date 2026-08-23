"""Generate the user-visible answer from reviewed workflow output."""

from collections.abc import Callable
from typing import Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate

from app.core.llm_factory import llm_factory

FINAL_RESPONSE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """你负责将已经完成审核的候选答案整理为最终用户回复。
必须遵守以下规则：
1. 只能使用候选答案中已有的事实、结论、引用和错误说明，不得补充新事实。
2. 保留所有形如 [来源N] 的引用编号，不得修改编号或虚构引用。
3. 不得隐藏候选答案中的失败、部分完成、证据不足或质量检查说明。
4. 删除 Agent 内部标签等影响阅读的包装，使用清晰、自然、可读的中文表达。
5. 直接输出最终回答，不解释你的整理过程。""",
        ),
        (
            "human",
            """原始请求：
<user_request>
{user_query}
</user_request>

候选答案：
<candidate_answer>
{candidate_answer}
</candidate_answer>""",
        ),
    ]
)


class FinalResponseGenerator(Protocol):
    async def generate(
        self,
        user_query: str,
        candidate_answer: str,
        *,
        event_sink: Callable[[str], None] | None = None,
    ) -> str: ...


class LLMFinalResponseGenerator:
    """Stream only the final presentation pass, after routing and review are complete."""

    def __init__(self, model: BaseChatModel | None = None) -> None:
        self._model = model

    async def generate(
        self,
        user_query: str,
        candidate_answer: str,
        *,
        event_sink: Callable[[str], None] | None = None,
    ) -> str:
        model = self._model or llm_factory.create_chat_model(role="finalizer")
        messages = FINAL_RESPONSE_PROMPT.format_messages(
            user_query=user_query,
            candidate_answer=candidate_answer,
        )
        chunks: list[str] = []
        async for message in model.astream(messages):
            text = self._message_text(message)
            if not text:
                continue
            chunks.append(text)
            if event_sink is not None:
                event_sink(text)
        answer = "".join(chunks).strip()
        if not answer:
            raise ValueError("最终回答生成结果为空")
        return answer

    @staticmethod
    def _message_text(message: BaseMessage) -> str:
        if isinstance(message.content, str):
            return message.content
        return "".join(
            str(block.get("text", ""))
            for block in message.content
            if isinstance(block, dict) and block.get("type") == "text"
        )


final_response_generator = LLMFinalResponseGenerator()
