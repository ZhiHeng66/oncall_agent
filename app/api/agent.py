"""Unified JSON and SSE API for the Multi-Agent execution path."""

import json

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.models.agent import (
    AgentRunRequest,
    AgentRunResponse,
    AgentThreadDeleteResponse,
    AgentThreadResponse,
)
from app.services.multi_agent_service import multi_agent_service

router = APIRouter(prefix="/agent")


@router.post("/run", response_model=AgentRunResponse)
async def run_agent(request: AgentRunRequest) -> AgentRunResponse:
    return await multi_agent_service.run(request)


@router.post("/run_stream")
async def stream_agent(request: AgentRunRequest, http_request: Request) -> EventSourceResponse:
    async def event_generator():
        event_stream = multi_agent_service.stream(request)
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
async def get_run(run_id: str) -> AgentRunResponse:
    result = await multi_agent_service.get_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return result


@router.get("/threads/{thread_id}", response_model=AgentThreadResponse)
async def get_thread(thread_id: str) -> AgentThreadResponse:
    return await multi_agent_service.get_thread(thread_id)


@router.delete("/threads/{thread_id}", response_model=AgentThreadDeleteResponse)
async def delete_thread(thread_id: str) -> AgentThreadDeleteResponse:
    return await multi_agent_service.clear_thread(thread_id)
