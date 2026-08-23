"""Task step executor with bounded delegation to Specialist Agents."""

from typing import Protocol

from langchain_core.language_models.chat_models import BaseChatModel

from app.agent.contracts import AgentTask, TaskStepResult
from app.agent.specialists.data.agent import DataAgent, data_agent
from app.agent.specialists.knowledge.agent import KnowledgeAgent, knowledge_agent
from app.agent.specialists.task.prompt import DIRECT_STEP_PROMPT, STEP_ROUTE_PROMPT
from app.agent.specialists.task.schemas import StepRoute
from app.core.llm_factory import llm_factory


class TaskStepExecutor(Protocol):
    async def execute(
        self,
        task: AgentTask,
        step: str,
        past_steps: list[TaskStepResult],
    ) -> TaskStepResult: ...


class SpecialistTaskStepExecutor:
    def __init__(
        self,
        knowledge: KnowledgeAgent = knowledge_agent,
        data: DataAgent = data_agent,
        model: BaseChatModel | None = None,
    ) -> None:
        self._knowledge = knowledge
        self._data = data
        self._model = model

    async def execute(
        self,
        task: AgentTask,
        step: str,
        past_steps: list[TaskStepResult],
    ) -> TaskStepResult:
        model = self._model or llm_factory.create_chat_model(role="task")
        history = self._format_history(past_steps)
        route_chain = STEP_ROUTE_PROMPT | model.with_structured_output(StepRoute)
        raw_route = await route_chain.ainvoke(
            {"objective": task.objective, "step": step, "history": history}
        )
        route = (
            raw_route if isinstance(raw_route, StepRoute) else StepRoute.model_validate(raw_route)
        )

        if route.target == "direct":
            response = await (DIRECT_STEP_PROMPT | model).ainvoke(
                {"objective": task.objective, "step": step, "history": history}
            )
            content = getattr(response, "content", response)
            return TaskStepResult(
                step=step,
                status="success",
                summary=str(content),
                delegated_agent="direct",
            )

        subtask = AgentTask(
            task_id=f"{task.task_id}:step:{len(past_steps) + 1}",
            objective=route.objective,
            context=f"总任务：{task.objective}\n已有结果：{history}",
            expected_output=route.expected_output,
            constraints=task.constraints,
            preferred_tools=route.preferred_tools,
        )
        result = (
            await self._knowledge.run(subtask)
            if route.target == "knowledge"
            else await self._data.run(subtask)
        )
        return TaskStepResult(
            step=step,
            status=result.status,
            summary=result.summary,
            delegated_agent=route.target,
            evidence=result.evidence,
            errors=result.errors,
        )

    @staticmethod
    def _format_history(past_steps: list[TaskStepResult]) -> str:
        if not past_steps:
            return "无"
        return "\n".join(f"- {item.step} [{item.status}]: {item.summary}" for item in past_steps)
