"""Validate the load client's SSE parsing and measurement without live services."""

import argparse
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

script_path = Path(__file__).resolve().parents[2] / "tools" / "load_test_agent.py"
spec = importlib.util.spec_from_file_location("load_test_agent", script_path)
assert spec and spec.loader
load_test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(load_test)


@pytest.mark.asyncio
async def test_multiline_sse_and_heartbeat() -> None:
    response = httpx.Response(
        200, content=b': ping\n\ndata: {"type":\ndata: "run_started"}\n\n'
    )
    assert [item async for item in load_test.sse_events(response)] == [{"type": "run_started"}]


@pytest.mark.asyncio
async def test_incomplete_record_rejected() -> None:
    with pytest.raises(ValueError, match="incomplete_sse_record"):
        _ = [item async for item in load_test.sse_events(
            httpx.Response(200, content=b'data: {"type": "run_started"}\n')
        )]


def build_test(tmp_path):
    args = argparse.Namespace(
        output=tmp_path, base_url="http://test", window=0, timeout=5, pause=0,
        max_requests=579, max_duration=7200,
    )
    return load_test.LoadTest(args)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "partial", "failed"])
async def test_terminal_business_status_is_not_event_name(tmp_path, status) -> None:
    test = build_test(tmp_path)
    result = {"run_id": "r1", "thread_id": "t1", "status": status,
              "answer": "answer", "sources": [], "errors": []}
    events = [
        {"type": "run_started", "data": {}},
        {"type": "content_delta", "data": {"content": "answer"}},
        {"type": "run_failed" if status == "failed" else "run_completed",
         "data": {"result": result}},
    ]
    for index, event in enumerate(events):
        event.update(run_id="r1", thread_id="t1", event_id=str(index))
    body = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    async with httpx.AsyncClient(
        base_url="http://test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)),
    ) as client:
        row = await test.request(client, "b1", 1, "data", 0, "t1", 0)
    assert row["business_status"] == status
    assert row["protocol_ok"]
    assert row["terminal_count"] == 1
    summary = load_test.summarize([row])
    assert summary["completed_duration_samples"] == (1 if status == "completed" else 0)


@pytest.mark.asyncio
async def test_duplicate_terminal_and_wrong_thread_fail(tmp_path) -> None:
    test = build_test(tmp_path)
    events = [
        {"type": "run_started", "data": {}},
        {"type": "run_failed", "data": {"error": "failed"}},
        {"type": "run_failed", "data": {"error": "failed"}},
    ]
    for index, event in enumerate(events):
        event.update(run_id="r1", thread_id="wrong", event_id=str(index))
    body = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    async with httpx.AsyncClient(
        base_url="http://test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)),
    ) as client:
        row = await test.request(client, "b1", 1, "data", 0, "t1", 0)
    assert not row["protocol_ok"]
    assert row["terminal_count"] == 2


@pytest.mark.asyncio
async def test_http_200_without_terminal_is_not_success(tmp_path) -> None:
    test = build_test(tmp_path)
    async with httpx.AsyncClient(
        base_url="http://test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=": ping\n\n")),
    ) as client:
        row = await test.request(client, "b1", 1, "data", 0, "t1", 0)
    assert row["business_status"] == "missing_terminal"
    assert not row["protocol_ok"]


@pytest.mark.parametrize(
    ("errors", "expected"),
    [
        (["Error code: 400; type: Arrearage"], "model_billing_or_account"),
        (["429 Too Many Requests"], "model_rate_limit"),
        (["没有可用于该任务的只读数据工具。"], "tool_or_mcp"),
    ],
)
def test_account_billing_is_not_classified_as_concurrency_rate_limit(errors, expected) -> None:
    assert load_test.category(errors) == expected
