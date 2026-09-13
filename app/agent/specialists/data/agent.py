"""Read-only Data Agent for MCP and controlled external data tools."""

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from langchain_core.language_models.chat_models import BaseChatModel

from app.agent.contracts import AgentResult, AgentStatus, AgentTask, Evidence
from app.agent.mcp_client import sanitize_error_message
from app.agent.memory.policy import dynamic_evidence_valid_until, query_scope_fingerprint
from app.agent.specialists.data.prompt import DATA_TOOL_PLAN_PROMPT
from app.agent.specialists.data.schemas import DataToolPlan
from app.agent.specialists.data.utils import sanitize_tool_output
from app.config import Settings, config
from app.core.llm_factory import llm_factory
from app.tools.registry import RegisteredTool, ToolRegistry, ToolSpec, tool_registry


class DataToolPlanner(Protocol):
    async def plan(self, task: AgentTask, tools: list[ToolSpec]) -> DataToolPlan: ...


class LLMDataToolPlanner:
    def __init__(self, model: BaseChatModel | None = None) -> None:
        self._model = model or llm_factory.create_chat_model(role="specialist")

    async def plan(self, task: AgentTask, tools: list[ToolSpec]) -> DataToolPlan:
        tool_text = "\n".join(
            f"- {item.name}: {item.description}; input_schema={item.input_schema}" for item in tools
        )
        chain = DATA_TOOL_PLAN_PROMPT | self._model.with_structured_output(DataToolPlan)
        response = await chain.ainvoke(
            {
                "objective": task.objective,
                "context": task.context or "无",
                "constraints": "；".join(task.constraints) or "无",
                "tools": tool_text,
            }
        )
        return (
            response
            if isinstance(response, DataToolPlan)
            else DataToolPlan.model_validate(response)
        )


class DataAgent:
    """Plan and execute bounded read-only queries, preserving failures as data."""

    name = "data"

    def __init__(
        self,
        registry: ToolRegistry = tool_registry,
        planner: DataToolPlanner | None = None,
        settings: Settings = config,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._registry = registry
        self._planner = planner
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(
        self,
        task: AgentTask,
        *,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentResult:
        try:
            registered = await self._registry.get_registered_tools_for_agent("data")
        except Exception as exc:
            return self._failed(task, f"外部数据工具发现失败: {sanitize_error_message(str(exc))}")

        registered = [item for item in registered if item.spec.read_only]
        if task.preferred_tools:
            preferred = set(task.preferred_tools)
            registered = [item for item in registered if item.spec.name in preferred]
        if not registered:
            return self._failed(task, "没有可用于该任务的只读数据工具。")

        planner = self._planner or LLMDataToolPlanner()
        try:
            plan = await planner.plan(task, [item.spec for item in registered])
        except Exception as exc:
            return self._failed(task, f"数据工具规划失败: {sanitize_error_message(str(exc))}")

        allowed = {item.spec.name: item for item in registered}
        calls = plan.calls[: self._settings.data_max_tool_calls]
        invalid_names = [call.tool_name for call in calls if call.tool_name not in allowed]
        calls = [call for call in calls if call.tool_name in allowed]
        errors = [f"拒绝未授权工具: {name}" for name in invalid_names]
        if not calls:
            return self._failed(task, errors[0] if errors else "未生成可执行的数据查询。")

        evidence: list[Evidence] = []
        for call in calls:
            registered_tool = allowed[call.tool_name]
            started = time.perf_counter()
            self._emit_tool_event(
                event_sink,
                "tool_started",
                registered_tool,
                status="running",
            )
            try:
                async with asyncio.timeout(registered_tool.spec.timeout_seconds):
                    output = await registered_tool.tool.ainvoke(call.arguments)
            except Exception as exc:
                errors.append(f"工具 {call.tool_name} 调用失败: {sanitize_error_message(str(exc))}")
                self._emit_tool_event(
                    event_sink,
                    "tool_completed",
                    registered_tool,
                    status="failed",
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
                continue
            evidence.append(
                self._to_evidence(
                    registered_tool,
                    output,
                    arguments=call.arguments,
                    observed_at=self._clock(),
                )
            )
            self._emit_tool_event(
                event_sink,
                "tool_completed",
                registered_tool,
                status="success",
                duration_ms=(time.perf_counter() - started) * 1000,
            )

        if evidence and errors:
            status: AgentStatus = "partial"
        elif evidence:
            status = "success"
        else:
            status = "failed"
        summary = self._summarize(evidence, errors)
        return AgentResult(
            agent_name=self.name,
            task_id=task.task_id,
            status=status,
            summary=summary,
            evidence=evidence,
            errors=errors,
        )

    def _to_evidence(
        self,
        registered: RegisteredTool,
        output: object,
        *,
        arguments: dict[str, Any],
        observed_at: datetime,
    ) -> Evidence:
        spec = registered.spec
        return Evidence(
            source_type="mcp" if spec.source == "mcp" else "tool",
            source_name=spec.server_name or spec.name,
            source_id=spec.name,
            content=sanitize_tool_output(output, self._settings.tool_result_max_chars),
            metadata={
                "tool_name": spec.name,
                "server_name": spec.server_name,
                "category": spec.category,
            },
            observed_at=observed_at,
            temporal_scope="snapshot",
            valid_until=dynamic_evidence_valid_until(
                observed_at,
                tool_name=spec.name,
                server_name=spec.server_name,
                category=spec.category,
                settings=self._settings,
            ),
            query_scope=query_scope_fingerprint(arguments),
        )

    @staticmethod
    def _summarize(evidence: list[Evidence], errors: list[str]) -> str:
        parts = [f"{item.source_id}: {item.content}" for item in evidence]
        if errors:
            parts.append("未完成项：" + "；".join(errors))
        return "\n".join(parts)

    def _failed(self, task: AgentTask, error: str) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            task_id=task.task_id,
            status="failed",
            summary=error,
            errors=[error],
        )

    @staticmethod
    def _emit_tool_event(
        event_sink: Callable[[dict[str, Any]], None] | None,
        event_type: str,
        registered: RegisteredTool,
        *,
        status: str,
        duration_ms: float | None = None,
    ) -> None:
        if event_sink is None:
            return
        data: dict[str, Any] = {
            "tool_name": registered.spec.name,
            "server_name": registered.spec.server_name,
            "status": status,
        }
        if duration_ms is not None:
            data["duration_ms"] = duration_ms
        event_sink({"type": event_type, "agent": "data", "data": data})


data_agent = DataAgent()
