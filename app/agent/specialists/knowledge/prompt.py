"""Prompt strategy for grounded knowledge answers."""

from langchain_core.prompts import ChatPromptTemplate

KNOWLEDGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """你是企业知识库专家。只能依据提供的证据回答，不得补充证据中不存在的事实。
每个事实性结论必须使用 [来源N] 标注。证据不足时明确说明，不要猜测。
回答应直接满足任务目标，并遵守输出要求和约束。""",
        ),
        (
            "user",
            """任务目标：{objective}
上下文：{context}
期望输出：{expected_output}
约束：{constraints}

检索证据：
{evidence_text}""",
        ),
    ]
)
