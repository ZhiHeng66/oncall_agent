"""Generic Task Agent replanner and final response generator."""

from typing import Protocol

from langchain_core.language_models.chat_models import BaseChatModel

from app.agent.contracts import AgentTask, TaskStepResult
from app.agent.specialists.task.prompt import TASK_REPLAN_PROMPT
from app.agent.specialists.task.schemas import ReplanDecision
from app.core.llm_factory import llm_factory


class TaskReplanner(Protocol):
    async def decide(
        self,
        task: AgentTask,
        remaining_steps: list[str],
        past_steps: list[TaskStepResult],
        remaining_budget: int,
    ) -> ReplanDecision: ...


class LLMTaskReplanner:
    def __init__(self, model: BaseChatModel | None = None) -> None:
        self._model = model

    async def decide(
        self,
        task: AgentTask,
        remaining_steps: list[str],
        past_steps: list[TaskStepResult],
        remaining_budget: int,
    ) -> ReplanDecision:
        history = "\n".join(f"- {item.step} [{item.status}]: {item.summary}" for item in past_steps)
        model = self._model or llm_factory.create_chat_model(role="task")
        chain = TASK_REPLAN_PROMPT | model.with_structured_output(ReplanDecision)
        response = await chain.ainvoke(
            {
                "objective": task.objective,
                "history": history or "无",
                "remaining": "；".join(remaining_steps) or "无",
                "budget": remaining_budget,
            }
        )
        return (
            response
            if isinstance(response, ReplanDecision)
            else ReplanDecision.model_validate(response)
        )
