"""对话接口

提供基于 RAG Agent 的普通对话和流式对话接口
"""

import json

from fastapi import APIRouter, HTTPException
from loguru import logger
from sse_starlette.sse import EventSourceResponse

from app.agent.mcp_client import format_exception_chain, sanitize_error_message
from app.models.agent import AgentRunRequest
from app.models.request import ChatRequest, ClearRequest
from app.models.response import ApiResponse, SessionInfoResponse
from app.services.multi_agent_service import multi_agent_service

router = APIRouter()


@router.post("/chat")
async def chat(request: ChatRequest):
    """快速对话接口
    {
        "code": 200,
        "message": "success",
        "data": {
            "success": true,
            "answer": "回答内容",
            "errorMessage": null
        }
    }

    Args:
        request: 对话请求

    Returns:
        统一格式的对话响应
    """
    try:
        logger.info(f"[会话 {request.id}] 收到统一 Agent 快速请求")
        result = await multi_agent_service.run(
            AgentRunRequest(user_id=request.id, thread_id=request.id, message=request.question)
        )

        logger.info(f"[会话 {request.id}] 快速对话完成")

        return {
            "code": 200,
            "message": "success",
            "data": {
                "success": result.status != "failed",
                "answer": result.answer if result.status != "failed" else None,
                "errorMessage": (
                    "；".join(result.errors) or result.answer if result.status == "failed" else None
                ),
            },
        }

    except Exception as e:
        detail = sanitize_error_message(str(e))
        logger.error(f"对话接口错误: {detail}")
        return {
            "code": 500,
            "message": "error",
            "data": {"success": False, "answer": None, "errorMessage": detail},
        }


@router.post("/chat_stream")
async def chat_stream(request: ChatRequest):
    """流式对话接口（基于 RAG Agent，SSE）

    返回 SSE 格式，data 字段为 JSON：

    工具调用事件:
    event: message
    data: {"type":"tool_call","data":{"tool":"工具名","status":"start|end","input":{...}}}

    内容流式事件:
    event: message
    data: {"type":"content","data":"内容块"}

    完成事件:
    event: message
    data: {"type":"done","data":{"answer":"完整答案","tool_calls":[...]}}

    Args:
        request: 对话请求

    Returns:
        SSE 事件流
    """
    logger.info(f"[会话 {request.id}] 收到统一 Agent 流式请求")

    async def event_generator():
        try:
            stream_request = AgentRunRequest(
                user_id=request.id,
                thread_id=request.id,
                message=request.question,
            )
            async for event in multi_agent_service.stream(stream_request):
                if event.type == "content_delta":
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {"type": "content", "data": event.data.get("content", "")},
                            ensure_ascii=False,
                        ),
                    }
                elif event.type == "run_completed":
                    result = event.data.get("result", {})
                    yield {
                        "event": "message",
                        "data": json.dumps(
                            {"type": "done", "data": {"answer": result.get("answer", "")}},
                            ensure_ascii=False,
                        ),
                    }
                    break
                elif event.type == "run_failed":
                    result = event.data.get("result", {})
                    detail = (
                        event.data.get("error")
                        or "；".join(result.get("errors", []))
                        or result.get("answer")
                        or "Agent 执行失败"
                    )
                    yield {
                        "event": "message",
                        "data": json.dumps({"type": "error", "data": detail}, ensure_ascii=False),
                    }
                    break

            logger.info(f"[会话 {request.id}] 流式对话完成")

        except Exception as e:
            detail = sanitize_error_message(format_exception_chain(e))
            logger.error(f"流式对话接口错误: {detail}")
            yield {
                "event": "message",
                "data": json.dumps({"type": "error", "data": detail}, ensure_ascii=False),
            }

    return EventSourceResponse(event_generator())


@router.post("/chat/clear", response_model=ApiResponse)
async def clear_session(request: ClearRequest):
    """清空会话历史

    Args:
        request: 清空请求

    Returns:
        操作结果
    """
    try:
        await multi_agent_service.clear_thread(request.session_id)
        logger.info(f"清空会话: {request.session_id}")

        return ApiResponse(
            status="success",
            message="会话已清空",
            data=None,
        )

    except Exception as e:
        logger.error(f"清空会话错误: {e}")
        raise HTTPException(status_code=500, detail=sanitize_error_message(str(e))) from e


@router.get("/chat/session/{session_id}", response_model=SessionInfoResponse)
async def get_session_info(session_id: str) -> SessionInfoResponse:
    """查询会话历史

    Args:
        session_id: 会话 ID

    Returns:
        会话信息
    """
    try:
        thread = await multi_agent_service.get_thread(session_id)

        return SessionInfoResponse(
            session_id=session_id,
            message_count=len(thread.history),
            history=thread.history,
        )

    except Exception as e:
        logger.error(f"获取会话信息错误: {e}")
        raise HTTPException(status_code=500, detail=sanitize_error_message(str(e))) from e
