import asyncio
from types import SimpleNamespace

import pytest

from app.agent.mcp_client import retry_interceptor, sanitize_error_message
from app.config import config


def test_sanitize_error_message_redacts_credentials_and_bounds_length() -> None:
    message = (
        "request failed Authorization: Bearer secret-token "
        "api_key=super-secret token=another-secret " + "x" * 500
    )

    sanitized = sanitize_error_message(message, max_length=120)

    assert "secret-token" not in sanitized
    assert "super-secret" not in sanitized
    assert "another-secret" not in sanitized
    assert "[REDACTED]" in sanitized
    assert len(sanitized) <= 120


@pytest.mark.asyncio
async def test_retry_interceptor_returns_clean_error_after_timeout(monkeypatch) -> None:
    monkeypatch.setattr(config, "tool_timeout_seconds", 0.001)
    request = SimpleNamespace(name="slow_tool", server_name="fake")

    async def slow_handler(_request):
        await asyncio.sleep(0.02)

    result = await retry_interceptor(request, slow_handler, max_retries=1, delay=0)

    assert result.isError is True
    assert result.content[0].text == "工具 slow_tool 在 1 次重试后仍然失败"
