"""Prompt for evidence-bound answer review."""

from langchain_core.prompts import ChatPromptTemplate

REVIEW_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """你是只读 Reviewer，不持有工具，也不能修改候选答案。
只能依据原始请求、候选答案、已有证据摘要和已记录错误检查：目标完整性、证据支撑、
引用完整性和错误披露。不得补充事实或新证据。评分范围 0 到 1。
通过时 issues 必须为空且 retry_target=none；不通过时必须描述问题并选择有限返工目标。""",
        ),
        (
            "human",
            """原始请求：{user_query}
候选答案：{candidate_answer}
证据摘要：{evidence_summary}
已记录错误：{errors}
案件上下文（过期动态观察不可作为当前事实）：{memory_context}""",
        ),
    ]
)
