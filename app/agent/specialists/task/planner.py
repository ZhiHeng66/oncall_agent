"""Generic Task Agent planner."""

from typing import Protocol

from langchain_core.language_models.chat_models import BaseChatModel

from app.agent.contracts import AgentTask
from app.agent.runtime.limits import RuntimeLimits, runtime_limits
from app.agent.specialists.task.prompt import TASK_PLAN_PROMPT
from app.agent.specialists.task.schemas import TaskPlan
from app.core.llm_factory import llm_factory
from app.tools.registry import ToolRegistry, tool_registry


class TaskPlanner(Protocol):
    async def plan(self, task: AgentTask) -> list[str]: ...


class LLMTaskPlanner:
    def __init__(
        self,
        registry: ToolRegistry = tool_registry,
        model: BaseChatModel | None = None,
        limits: RuntimeLimits = runtime_limits,
    ) -> None:
        self._registry = registry
        self._model = model
        self._limits = limits

    async def plan(self, task: AgentTask) -> list[str]:
        knowledge_specs = await self._registry.get_tool_descriptions("knowledge")
        data_specs = await self._registry.get_tool_descriptions("data")
        capabilities = "\n".join(
            [
                *[f"- Knowledge Agent: {item.description}" for item in knowledge_specs],
                *[f"- Data Agent/{item.name}: {item.description}" for item in data_specs],
            ]
        )
        model = self._model or llm_factory.create_chat_model(role="task")
        chain = TASK_PLAN_PROMPT | model.with_structured_output(TaskPlan)
        response = await chain.ainvoke(
            {
                "objective": task.objective,
                "context": task.context or "无",
                "expected_output": task.expected_output,
                "constraints": "；".join(task.constraints) or "无",
                "capabilities": capabilities or "无外部工具",
                "max_steps": self._limits.task_max_steps,
            }
        )
        plan = response if isinstance(response, TaskPlan) else TaskPlan.model_validate(response)
        return plan.steps[: self._limits.task_max_steps]
