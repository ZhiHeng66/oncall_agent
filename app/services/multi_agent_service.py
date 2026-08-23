"""Application service for unified JSON, SSE and in-memory run lookup."""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any, Protocol, cast
from uuid import uuid4

from app.agent.contracts import MultiAgentRunResult
from app.agent.graph import multi_agent_graph
from app.agent.mcp_client import sanitize_error_message
from app.agent.specialists.data.utils import sanitize_tool_output
from app.config import Settings, config
from app.models.agent import (
    AgentEventEnvelope,
    AgentEventType,
    AgentRouteSummary,
    AgentRunRequest,
    AgentRunResponse,
    AgentSource,
    AgentThreadDeleteResponse,
    AgentThreadResponse,
    PublicAgentName,
)
from app.services.event_service import AgentEventService, agent_event_service

PUBLIC_METADATA_KEYS = {
    "_file_name",
    "_source",
    "title",
    "relevance_score",
    "tool_name",
    "server_name",
    "category",
}


class MultiAgentRunner(Protocol):
    async def run(
        self,
        user_query: str,
        *,
        user_id: str,
        thread_id: str,
        run_id: str | None = None,
    ) -> MultiAgentRunResult: ...

    def stream(
        self,
        user_query: str,
        *,
        user_id: str,
        thread_id: str,
        run_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]: ...

    async def delete_run_state(self, thread_id: str, run_id: str) -> None: ...


class MultiAgentService:
    """Expose the graph without leaking internal state or raw provider responses."""

    def __init__(
        self,
        runner: MultiAgentRunner = multi_agent_graph,
        events: AgentEventService = agent_event_service,
        settings: Settings = config,
    ) -> None:
        self._runner = runner
        self._events = events
        self._settings = settings
        self._runs: dict[str, AgentRunResponse] = {}
        self._thread_runs: dict[str, list[str]] = {}
        self._thread_history: dict[str, list[dict[str, str]]] = {}
        self._store_lock = asyncio.Lock()

    async def run(self, request: AgentRunRequest) -> AgentRunResponse:
        run_id = str(uuid4())
        result = await self._runner.run(
            request.message,
            user_id=request.user_id,
            thread_id=request.thread_id,
            run_id=run_id,
        )
        response = self._to_response(result)
        await self._record(request, response)
        return response

    async def stream(
        self,
        request: AgentRunRequest,
    ) -> AsyncGenerator[AgentEventEnvelope, None]:
        run_id = str(uuid4())
        terminal_emitted = False
        yield self._events.create(
            run_id=run_id,
            thread_id=request.thread_id,
            event_type="run_started",
            data={"user_id": request.user_id},
        )
        try:
            async for event in self._runner.stream(
                request.message,
                user_id=request.user_id,
                thread_id=request.thread_id,
                run_id=run_id,
            ):
                if event.get("type") != "graph_completed":
                    yield self._from_internal_event(run_id, request.thread_id, event)
                    continue
                result = MultiAgentRunResult.model_validate(event["result"])
                response = self._to_response(result)
                await self._record(request, response)
                if response.answer:
                    yield self._events.create(
                        run_id=run_id,
                        thread_id=request.thread_id,
                        event_type="content_delta",
                        data={"content": response.answer},
                    )
                terminal_type: AgentEventType = (
                    "run_failed" if response.status == "failed" else "run_completed"
                )
                yield self._events.create(
                    run_id=run_id,
                    thread_id=request.thread_id,
                    event_type=terminal_type,
                    data={"result": response.model_dump(mode="json")},
                )
                terminal_emitted = True
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            detail = sanitize_error_message(str(exc))
            yield self._events.create(
                run_id=run_id,
                thread_id=request.thread_id,
                event_type="run_failed",
                data={"status": "failed", "error": detail},
            )
            terminal_emitted = True
        if not terminal_emitted:
            yield self._events.create(
                run_id=run_id,
                thread_id=request.thread_id,
                event_type="run_failed",
                data={"status": "failed", "error": "Agent 事件流未返回终态。"},
            )

    async def get_run(self, run_id: str) -> AgentRunResponse | None:
        async with self._store_lock:
            return self._runs.get(run_id)

    async def get_thread(self, thread_id: str) -> AgentThreadResponse:
        async with self._store_lock:
            return AgentThreadResponse(
                thread_id=thread_id,
                run_ids=list(self._thread_runs.get(thread_id, [])),
                history=list(self._thread_history.get(thread_id, [])),
            )

    async def clear_thread(self, thread_id: str) -> AgentThreadDeleteResponse:
        async with self._store_lock:
            run_ids = self._thread_runs.pop(thread_id, [])
            self._thread_history.pop(thread_id, None)
            for run_id in run_ids:
                self._runs.pop(run_id, None)
        for run_id in run_ids:
            await self._runner.delete_run_state(thread_id, run_id)
        return AgentThreadDeleteResponse(thread_id=thread_id, deleted_runs=len(run_ids))

    async def _record(self, request: AgentRunRequest, response: AgentRunResponse) -> None:
        async with self._store_lock:
            self._runs[response.run_id] = response
            self._thread_runs.setdefault(response.thread_id, []).append(response.run_id)
            history = self._thread_history.setdefault(response.thread_id, [])
            history.extend(
                [
                    {"role": "user", "content": request.message},
                    {"role": "assistant", "content": response.answer},
                ]
            )

    def _to_response(self, result: MultiAgentRunResult) -> AgentRunResponse:
        sources: list[AgentSource] = []
        seen_sources: set[tuple[str, str | None]] = set()
        for agent_result in result.agent_results:
            for evidence in agent_result.evidence:
                key = (evidence.source_name, evidence.source_id)
                if key in seen_sources:
                    continue
                seen_sources.add(key)
                sources.append(
                    AgentSource(
                        source_type=evidence.source_type,
                        source_name=evidence.source_name,
                        source_id=evidence.source_id,
                        excerpt=sanitize_tool_output(
                            evidence.content,
                            self._settings.api_source_excerpt_max_chars,
                        ),
                        metadata={
                            key: self._sanitize_metadata_value(value)
                            for key, value in evidence.metadata.items()
                            if key in PUBLIC_METADATA_KEYS
                        },
                    )
                )
        agents: list[PublicAgentName] = ["supervisor"]
        for agent_result in result.agent_results:
            name = agent_result.agent_name
            if name in {"knowledge", "data", "task"} and name not in agents:
                agents.append(cast(PublicAgentName, name))
        if result.review_history:
            agents.append("reviewer")
        route = (
            AgentRouteSummary(
                mode=result.route.mode,
                target_agents=result.route.target_agents,
                reason_summary=result.route.reason_summary,
            )
            if result.route
            else None
        )
        return AgentRunResponse(
            run_id=result.run_id,
            thread_id=result.thread_id,
            status=result.status,
            answer=self._sanitize_public_text(
                result.final_answer,
                self._settings.api_answer_max_chars,
            ),
            sources=sources,
            agents=agents,
            route=route,
            errors=[sanitize_error_message(error) for error in result.errors],
            termination_reason=result.termination_reason,
        )

    def _sanitize_metadata_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._sanitize_public_text(value, self._settings.api_source_excerpt_max_chars)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return sanitize_tool_output(value, self._settings.api_source_excerpt_max_chars)

    @staticmethod
    def _sanitize_public_text(value: str, max_chars: int) -> str:
        sanitized = "\n".join(
            sanitize_error_message(line, max_length=max(len(line), 4))
            for line in value.splitlines()
        )
        if len(sanitized) > max_chars:
            return f"{sanitized[: max_chars - 3]}..."
        return sanitized

    def _from_internal_event(
        self,
        run_id: str,
        thread_id: str,
        event: dict[str, Any],
    ) -> AgentEventEnvelope:
        event_type = event.get("type")
        allowed = {
            "route_decided",
            "agent_started",
            "agent_completed",
            "plan_created",
            "step_started",
            "tool_started",
            "tool_completed",
            "step_completed",
            "step_failed",
            "review_completed",
        }
        if event_type not in allowed:
            event_type = "step_completed"
        agent = event.get("agent")
        if agent not in {"supervisor", "knowledge", "data", "task", "reviewer"}:
            agent = None
        return self._events.create(
            run_id=run_id,
            thread_id=thread_id,
            event_type=cast(AgentEventType, event_type),
            agent=agent,
            data=event.get("data", {}),
        )


multi_agent_service = MultiAgentService()
