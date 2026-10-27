"""Application service for unified JSON, SSE, persistence and tracing."""

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from loguru import logger

from app.agent.contracts import MultiAgentRunResult
from app.agent.graph import multi_agent_graph
from app.agent.mcp_client import sanitize_error_message
from app.agent.memory.compressor import WorkingMemoryCompressor
from app.agent.memory.context import MemoryContextBuilder
from app.agent.memory.contracts import MemoryContextView, MemoryUsage, WorkingMemoryContext
from app.agent.memory.reducer import WorkingMemoryReducer
from app.agent.runtime.checkpointer import CheckpointerFactory
from app.agent.specialists.data.utils import sanitize_tool_output
from app.config import Settings, config
from app.models.agent import (
    AgentEventEnvelope,
    AgentEventType,
    AgentMetricsResponse,
    AgentRouteSummary,
    AgentRunMetrics,
    AgentRunRequest,
    AgentRunResponse,
    AgentRunTraceResponse,
    AgentSource,
    AgentThreadDeleteResponse,
    AgentThreadResponse,
    AgentThreadSummary,
    PublicAgentName,
)
from app.services.event_service import AgentEventService, agent_event_service
from app.services.persistence import PersistenceFactory
from app.services.run_store import MemoryRunStore, RunStore
from app.services.thread_store import InMemoryThreadStore, PostgresThreadStore
from app.services.working_memory_store import WorkingMemoryStore

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
    def set_checkpointer(self, checkpointer: BaseCheckpointSaver) -> None: ...

    async def run(
        self,
        user_query: str,
        *,
        user_id: str,
        thread_id: str,
        run_id: str | None = None,
        working_memory: WorkingMemoryContext | None = None,
        memory_view: MemoryContextView | None = None,
        memory_usage: MemoryUsage | None = None,
    ) -> MultiAgentRunResult: ...

    def stream(
        self,
        user_query: str,
        *,
        user_id: str,
        thread_id: str,
        run_id: str | None = None,
        working_memory: WorkingMemoryContext | None = None,
        memory_view: MemoryContextView | None = None,
        memory_usage: MemoryUsage | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]: ...

    async def delete_run_state(self, user_id: str, thread_id: str, run_id: str) -> None: ...


class MultiAgentService:
    """Expose the graph without leaking internal state or raw provider responses."""

    def __init__(
        self,
        runner: MultiAgentRunner = multi_agent_graph,
        events: AgentEventService = agent_event_service,
        settings: Settings = config,
        store: RunStore | None = None,
        checkpointers: CheckpointerFactory | None = None,
        persistence: PersistenceFactory | None = None,
        working_memory_store: WorkingMemoryStore | None = None,
        memory_compressor: WorkingMemoryCompressor | None = None,
        thread_store: InMemoryThreadStore | PostgresThreadStore | None = None,
    ) -> None:
        self._runner = runner
        self._events = events
        self._settings = settings
        self._store = store or MemoryRunStore()
        self._working_memory_store = working_memory_store
        self._memory_compressor = memory_compressor or WorkingMemoryCompressor(settings)
        self._persistence = persistence or PersistenceFactory(settings, checkpointers)
        self._resources: AsyncExitStack | None = None
        self._thread_store = thread_store or InMemoryThreadStore()

    async def startup(self) -> None:
        """Open persistent resources and recompile the graph once per process."""

        if self._resources is not None:
            return
        self._resources = AsyncExitStack()
        await self._resources.__aenter__()
        persistence = await self._resources.enter_async_context(self._persistence.open())
        self._runner.set_checkpointer(persistence.checkpointer)
        self._store = persistence.run_store
        self._working_memory_store = persistence.working_memory_store
        self._thread_store = persistence.thread_store
        logger.info(
            "Agent persistence initialized backend={} working_memory_enabled={}",
            self._settings.checkpointer_backend,
            self._working_memory_store is not None,
        )

    async def shutdown(self) -> None:
        if self._resources is None:
            return
        await self._resources.aclose()
        self._resources = None

    async def run(self, request: AgentRunRequest, owner_user_id: UUID) -> AgentRunResponse:
        request = await self._resolve_request(request, owner_user_id)
        run_id = str(uuid4())
        started = time.perf_counter()
        memory, memory_view, memory_usage = await self._prepare_memory(request, owner_user_id)
        result = await self._runner.run(
            request.message,
            user_id=str(owner_user_id),
            thread_id=request.thread_id,
            run_id=run_id,
            working_memory=memory,
            memory_view=memory_view,
            memory_usage=memory_usage,
        )
        response = self._to_response(result)
        events = self._events_from_result(response, result)
        metrics = self._metrics_from_result(result, events, started)
        await self._complete_run(request, owner_user_id, response, events, metrics, memory, result)
        return response

    async def stream(
        self,
        request: AgentRunRequest,
        owner_user_id: UUID,
    ) -> AsyncGenerator[AgentEventEnvelope, None]:
        request = await self._resolve_request(request, owner_user_id)
        run_id = str(uuid4())
        started = time.perf_counter()
        memory, memory_view, memory_usage = await self._prepare_memory(request, owner_user_id)
        terminal_emitted = False
        content_emitted = False
        persisted_events: list[AgentEventEnvelope] = []
        start_event = self._events.create(
            run_id=run_id,
            thread_id=request.thread_id,
            event_type="run_started",
            data={},
        )
        persisted_events.append(start_event)
        yield start_event
        try:
            async for event in self._runner.stream(
                request.message,
                user_id=str(owner_user_id),
                thread_id=request.thread_id,
                run_id=run_id,
                working_memory=memory,
                memory_view=memory_view,
                memory_usage=memory_usage,
            ):
                if event.get("type") != "graph_completed":
                    if event.get("type") == "final_content_delta":
                        content = str(event.get("data", {}).get("content", ""))
                        if content:
                            content_event = self._events.create(
                                run_id=run_id,
                                thread_id=request.thread_id,
                                event_type="content_delta",
                                data={"content": content},
                                node="final_response",
                            )
                            persisted_events.append(content_event)
                            content_emitted = True
                            yield content_event
                        continue
                    public_event = self._from_internal_event(run_id, request.thread_id, event)
                    persisted_events.append(public_event)
                    yield public_event
                    continue
                result = MultiAgentRunResult.model_validate(event["result"])
                response = self._to_response(result)
                if response.answer and not content_emitted:
                    content_event = self._events.create(
                        run_id=run_id,
                        thread_id=request.thread_id,
                        event_type="content_delta",
                        data={"content": response.answer},
                    )
                    persisted_events.append(content_event)
                    yield content_event
                terminal_type: AgentEventType = (
                    "run_failed" if response.status == "failed" else "run_completed"
                )
                terminal_event = self._events.create(
                    run_id=run_id,
                    thread_id=request.thread_id,
                    event_type=terminal_type,
                    data={"result": response.model_dump(mode="json")},
                )
                persisted_events.append(terminal_event)
                await self._complete_run(
                    request, owner_user_id,
                    response,
                    persisted_events,
                    self._metrics_from_result(result, persisted_events, started),
                    memory,
                    result,
                )
                yield terminal_event
                terminal_emitted = True
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            detail = sanitize_error_message(str(exc))
            terminal_event = self._events.create(
                run_id=run_id,
                thread_id=request.thread_id,
                event_type="run_failed",
                data={"status": "failed", "error": detail},
            )
            persisted_events.append(terminal_event)
            response = AgentRunResponse(
                run_id=run_id,
                thread_id=request.thread_id,
                status="failed",
                answer="Agent 事件流执行失败。",
                errors=[detail],
                termination_reason="stream_failed",
            )
            await self._record_failure_if_possible(
                request, owner_user_id,
                response,
                persisted_events,
                self._metrics_from_events(response, persisted_events, started),
            )
            yield terminal_event
            terminal_emitted = True
        if not terminal_emitted:
            terminal_event = self._events.create(
                run_id=run_id,
                thread_id=request.thread_id,
                event_type="run_failed",
                data={"status": "failed", "error": "Agent 事件流未返回终态。"},
            )
            persisted_events.append(terminal_event)
            response = AgentRunResponse(
                run_id=run_id,
                thread_id=request.thread_id,
                status="failed",
                answer="Agent 事件流未返回终态。",
                errors=["Agent 事件流未返回终态。"],
                termination_reason="missing_terminal",
            )
            await self._record_failure_if_possible(
                request, owner_user_id,
                response,
                persisted_events,
                self._metrics_from_events(response, persisted_events, started),
            )
            yield terminal_event

    async def _prepare_memory(
        self, request: AgentRunRequest, owner_user_id: UUID
    ) -> tuple[WorkingMemoryContext | None, MemoryContextView, MemoryUsage]:
        if self._working_memory_store is None:
            return None, MemoryContextView(), MemoryUsage()
        try:
            memory = await self._working_memory_store.load(owner_user_id, request.thread_id)
            recent_turns = await self._store.get_recent_thread(
                owner_user_id, request.thread_id, self._settings.working_memory_recent_turns
            )
            memory_view, memory_usage = MemoryContextBuilder(self._settings).build(
                memory, recent_turns
            )
            return memory, memory_view, memory_usage
        except Exception as exc:
            logger.warning(
                "Working memory load failed thread_id={} detail={}",
                request.thread_id,
                sanitize_error_message(str(exc)),
            )
        return None, MemoryContextView(), MemoryUsage(status="failed")

    async def _resolve_request(
        self, request: AgentRunRequest, owner_user_id: UUID
    ) -> AgentRunRequest:
        if not request.thread_id:
            thread = await self._thread_store.create(owner_user_id)
            return request.model_copy(update={"thread_id": thread.thread_id})
        if await self._thread_store.get_owned(owner_user_id, request.thread_id) is None:
            raise LookupError("Agent thread not found")
        return request

    async def resolve_request(
        self, request: AgentRunRequest, owner_user_id: UUID
    ) -> AgentRunRequest:
        """Resolve a server-created thread or verify a supplied owned thread before streaming."""
        return await self._resolve_request(request, owner_user_id)

    async def create_thread(self, owner_user_id: UUID) -> str:
        """Create a server-owned thread for clients that need an ID before their first run."""
        return (await self._thread_store.create(owner_user_id)).thread_id

    async def list_threads(self, owner_user_id: UUID) -> list[AgentThreadSummary]:
        return [
            AgentThreadSummary(
                thread_id=item.thread_id,
                title=item.title,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in await self._thread_store.list_owned(owner_user_id)
        ]

    async def _record_failure_if_possible(
        self,
        request: AgentRunRequest,
        owner_user_id: UUID,
        response: AgentRunResponse,
        events: list[AgentEventEnvelope],
        metrics: AgentRunMetrics,
    ) -> None:
        """Keep an SSE failure terminal observable when its persistence also fails."""

        try:
            await self._record(request, owner_user_id, response, events, metrics)
        except Exception as exc:
            logger.error(
                "Agent failure trace persistence failed run_id={} thread_id={} error={}",
                response.run_id,
                response.thread_id,
                sanitize_error_message(str(exc)),
            )

    async def get_run(self, owner_user_id: UUID, run_id: str) -> AgentRunResponse | None:
        return await self._store.get_run(owner_user_id, run_id)

    async def get_trace(self, owner_user_id: UUID, run_id: str) -> AgentRunTraceResponse | None:
        return await self._store.get_trace(owner_user_id, run_id)

    async def get_metrics(self, owner_user_id: UUID | None = None) -> AgentMetricsResponse:
        return await self._store.metrics(owner_user_id)

    async def get_thread(self, thread_id: str, owner_user_id: UUID) -> AgentThreadResponse:
        if await self._thread_store.get_owned(owner_user_id, thread_id) is None:
            raise LookupError("Agent thread not found")
        return await self._store.get_thread(owner_user_id, thread_id)

    async def clear_thread(self, thread_id: str, owner_user_id: UUID) -> AgentThreadDeleteResponse:
        if await self._thread_store.get_owned(owner_user_id, thread_id) is None:
            raise LookupError("Agent thread not found")
        if self._working_memory_store is not None:
            await self._working_memory_store.delete_thread(owner_user_id, thread_id)
        run_ids = await self._store.delete_thread(owner_user_id, thread_id)
        for run_id in run_ids:
            await self._runner.delete_run_state(str(owner_user_id), thread_id, run_id)
        if not await self._thread_store.delete_owned(owner_user_id, thread_id):
            raise RuntimeError("Agent thread disappeared during deletion")
        return AgentThreadDeleteResponse(thread_id=thread_id, deleted_runs=len(run_ids))

    async def _complete_run(
        self,
        request: AgentRunRequest,
        owner_user_id: UUID,
        response: AgentRunResponse,
        events: list[AgentEventEnvelope],
        metrics: AgentRunMetrics,
        loaded_memory: WorkingMemoryContext | None,
        result: MultiAgentRunResult,
    ) -> None:
        """Persist the public run first, then attempt bounded memory mutation without masking it."""

        await self._record(request, owner_user_id, response, events, metrics)
        await self._thread_store.touch_owned(owner_user_id, request.thread_id)
        await self._update_working_memory(request, owner_user_id, result, loaded_memory)

    async def _update_working_memory(
        self,
        request: AgentRunRequest,
        owner_user_id: UUID,
        result: MultiAgentRunResult,
        loaded_memory: WorkingMemoryContext | None,
    ) -> None:
        if self._working_memory_store is None:
            return
        run_id = result.run_id
        reducer = WorkingMemoryReducer(self._settings)
        update, additions = reducer.build_update(request.message, result)
        current = loaded_memory
        for attempt in range(self._settings.working_memory_update_max_retries + 1):
            try:
                candidate = reducer.apply(
                    current,
                    thread_id=request.thread_id,
                    owner_user_id=str(owner_user_id),
                    run_id=run_id,
                    update=update,
                    additions=additions,
                )
                if candidate is None:
                    return
                uncompressed_size = self._memory_compressor.estimated_chars(candidate)
                candidate = await self._memory_compressor.maybe_compress(candidate)
                if candidate.compressed_summary:
                    logger.info(
                        "Working memory compressed run_id={} thread_id={} chars_before={} chars_after={}",
                        run_id,
                        request.thread_id,
                        uncompressed_size,
                        self._memory_compressor.estimated_chars(candidate),
                    )
                saved = (
                    await self._working_memory_store.create(candidate)
                    if current is None
                    else await self._working_memory_store.compare_and_swap(
                        candidate, current.version
                    )
                )
                if saved:
                    logger.info(
                        "Working memory updated run_id={} thread_id={} version={} item_count={}",
                        run_id,
                        request.thread_id,
                        candidate.version,
                        len(candidate.items),
                    )
                    return
                current = await self._working_memory_store.load(owner_user_id, request.thread_id)
            except Exception as exc:
                logger.warning(
                    "Working memory update failed run_id={} thread_id={} attempt={} detail={}",
                    run_id,
                    request.thread_id,
                    attempt + 1,
                    sanitize_error_message(str(exc)),
                )
                return
        logger.warning(
            "Working memory CAS retry exhausted run_id={} thread_id={}", run_id, request.thread_id
        )

    async def _record(
        self,
        request: AgentRunRequest,
        owner_user_id: UUID,
        response: AgentRunResponse,
        events: list[AgentEventEnvelope],
        metrics: AgentRunMetrics,
    ) -> None:
        await self._store.save(owner_user_id, request.message, response, events, metrics)
        logger.info(
            "Agent run completed run_id={} thread_id={} agents={} duration_ms={:.2f} status={}",
            response.run_id,
            response.thread_id,
            ",".join(response.agents),
            metrics.duration_ms,
            response.status,
        )

    def _events_from_result(
        self,
        response: AgentRunResponse,
        result: MultiAgentRunResult,
    ) -> list[AgentEventEnvelope]:
        events = [
            self._events.create(
                run_id=response.run_id,
                thread_id=response.thread_id,
                event_type="run_started",
                data={},
            )
        ]
        if response.route:
            events.append(
                self._events.create(
                    run_id=response.run_id,
                    thread_id=response.thread_id,
                    event_type="route_decided",
                    agent="supervisor",
                    node="supervisor",
                    data=response.route.model_dump(mode="json"),
                )
            )
        for record in result.dispatch_records:
            events.append(
                self._events.create(
                    run_id=response.run_id,
                    thread_id=response.thread_id,
                    event_type="agent_completed",
                    agent=record.agent_name,
                    node=f"{record.agent_name}_agent",
                    data=record.model_dump(mode="json"),
                )
            )
        for review in result.review_history:
            events.append(
                self._events.create(
                    run_id=response.run_id,
                    thread_id=response.thread_id,
                    event_type="review_completed",
                    agent="reviewer",
                    node="reviewer",
                    data=review.model_dump(mode="json"),
                )
            )
        terminal_type: AgentEventType = (
            "run_failed" if response.status == "failed" else "run_completed"
        )
        events.append(
            self._events.create(
                run_id=response.run_id,
                thread_id=response.thread_id,
                event_type=terminal_type,
                data={"status": response.status},
            )
        )
        return events

    @staticmethod
    def _metrics_from_result(
        result: MultiAgentRunResult,
        events: list[AgentEventEnvelope],
        started: float,
    ) -> AgentRunMetrics:
        response_status = result.status
        return AgentRunMetrics(
            duration_ms=(time.perf_counter() - started) * 1000,
            dispatch_count=len(result.dispatch_records),
            tool_call_count=sum(event.type == "tool_completed" for event in events),
            task_step_count=sum(
                event.type in {"step_completed", "step_failed"} for event in events
            ),
            failed=response_status == "failed",
            timed_out=any("timeout" in error.lower() or "超时" in error for error in result.errors),
        )

    @staticmethod
    def _metrics_from_events(
        response: AgentRunResponse,
        events: list[AgentEventEnvelope],
        started: float,
    ) -> AgentRunMetrics:
        return AgentRunMetrics(
            duration_ms=(time.perf_counter() - started) * 1000,
            dispatch_count=sum(event.type == "agent_completed" for event in events),
            tool_call_count=sum(event.type == "tool_completed" for event in events),
            task_step_count=sum(
                event.type in {"step_completed", "step_failed"} for event in events
            ),
            failed=response.status == "failed",
            timed_out=any(
                "timeout" in error.lower() or "超时" in error for error in response.errors
            ),
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
            node=self._event_node(cast(AgentEventType, event_type), agent),
            data=event.get("data", {}),
        )

    @staticmethod
    def _event_node(
        event_type: AgentEventType,
        agent: PublicAgentName | None,
    ) -> str | None:
        if agent == "supervisor":
            return "supervisor"
        if agent == "reviewer":
            return "reviewer"
        if agent in {"knowledge", "data", "task"}:
            return f"{agent}_agent"
        if event_type in {"run_started", "run_completed", "run_failed", "content_delta"}:
            return "api_service"
        return None


multi_agent_service = MultiAgentService()
