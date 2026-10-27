"""Unified JSON and SSE API for the Multi-Agent execution path."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.auth.contracts import CurrentUser
from app.auth.dependencies import get_current_user
from app.models.agent import (
    AgentMetricsResponse,
    AgentRunRequest,
    AgentRunResponse,
    AgentRunTraceResponse,
    AgentThreadDeleteResponse,
    AgentThreadResponse,
    AgentThreadSummary,
)
from app.services.multi_agent_service import multi_agent_service

router = APIRouter(prefix="/agent")


@router.post("/run", response_model=AgentRunResponse)
async def run_agent(
    request: AgentRunRequest, current_user: Annotated[CurrentUser, Depends(get_current_user)]
) -> AgentRunResponse:
    try:
        return await multi_agent_service.run(request, current_user.user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Agent thread not found") from exc


@router.post("/threads", response_model=AgentThreadResponse)
async def create_thread(
    current_user: Annotated[CurrentUser, Depends(get_current_user)]
) -> AgentThreadResponse:
    thread_id = await multi_agent_service.create_thread(current_user.user_id)
    return AgentThreadResponse(thread_id=thread_id)


@router.get("/threads", response_model=list[AgentThreadSummary])
async def list_threads(
    current_user: Annotated[CurrentUser, Depends(get_current_user)]
) -> list[AgentThreadSummary]:
    return await multi_agent_service.list_threads(current_user.user_id)


@router.post("/run_stream")
async def stream_agent(
    request: AgentRunRequest,
    http_request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> EventSourceResponse:
    try:
        request = await multi_agent_service.resolve_request(request, current_user.user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Agent thread not found") from exc

    async def event_generator():
        event_stream = multi_agent_service.stream(request, current_user.user_id)
        try:
            async for event in event_stream:
                if await http_request.is_disconnected():
                    break
                yield {
                    "event": "message",
                    "id": event.event_id,
                    "data": json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
                }
        finally:
            await event_stream.aclose()

    return EventSourceResponse(
        event_generator(),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
async def get_run(
    run_id: str, current_user: Annotated[CurrentUser, Depends(get_current_user)]
) -> AgentRunResponse:
    result = await multi_agent_service.get_run(current_user.user_id, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return result


@router.get("/runs/{run_id}/trace", response_model=AgentRunTraceResponse)
async def get_run_trace(
    run_id: str, current_user: Annotated[CurrentUser, Depends(get_current_user)]
) -> AgentRunTraceResponse:
    result = await multi_agent_service.get_trace(current_user.user_id, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return result


@router.get("/metrics", response_model=AgentMetricsResponse)
async def get_metrics(
    current_user: Annotated[CurrentUser, Depends(get_current_user)]
) -> AgentMetricsResponse:
    return await multi_agent_service.get_metrics(
        None if current_user.role == "admin" else current_user.user_id
    )


@router.get("/threads/{thread_id}", response_model=AgentThreadResponse)
async def get_thread(
    thread_id: str, current_user: Annotated[CurrentUser, Depends(get_current_user)]
) -> AgentThreadResponse:
    try:
        return await multi_agent_service.get_thread(thread_id, current_user.user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Agent thread not found") from exc


@router.delete("/threads/{thread_id}", response_model=AgentThreadDeleteResponse)
async def delete_thread(
    thread_id: str, current_user: Annotated[CurrentUser, Depends(get_current_user)]
) -> AgentThreadDeleteResponse:
    try:
        return await multi_agent_service.clear_thread(thread_id, current_user.user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Agent thread not found") from exc
