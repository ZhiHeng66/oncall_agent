"""Generic prompts for Task Agent planning, delegation, and replanning."""

from langchain_core.prompts import ChatPromptTemplate

TASK_PLAN_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """你是复杂任务规划器。将目标拆成有依赖关系、可验证、按顺序执行的最少步骤。
只能参考给定能力，不执行工具。不得使用告警诊断等固定业务模板。
计划步骤不得超过 {max_steps}。""",
        ),
        (
            "user",
            """目标：{objective}
上下文：{context}
期望输出：{expected_output}
约束：{constraints}
可用能力：
{capabilities}""",
        ),
    ]
)

STEP_ROUTE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """你负责把当前步骤委派给一个有限目标：
- knowledge：需要企业文档证据；
- data：需要只读外部数据或工具；
- direct：只需基于已有步骤结果整理、计算或总结，不调用工具。
不得生成其他目标。""",
        ),
        (
            "user",
            """总任务：{objective}
当前步骤：{step}
已有结果：{history}""",
        ),
    ]
)

DIRECT_STEP_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "基于已有步骤结果完成当前步骤。不得编造新事实；失败和缺失信息必须保留。",
        ),
        (
            "user",
            """总任务：{objective}
当前步骤：{step}
已有结果：{history}""",
        ),
    ]
)

TASK_REPLAN_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """根据任务、已执行结果和剩余步骤决定 continue、replan 或 respond。
优先在信息足够时 respond。replan 只能给出剩余预算内的必要步骤。
最终回答必须披露失败与信息不足，不得把失败改写成成功。""",
        ),
        (
            "user",
            """任务：{objective}
已执行结果：
{history}
剩余步骤：{remaining}
剩余预算：{budget}""",
        ),
    ]
)
