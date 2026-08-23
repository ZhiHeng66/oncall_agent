from typing import Any

import pytest
from langchain_core.tools import tool

from app.config import Settings
from app.tools.mcp_provider import DuplicateMCPToolError, MCPToolProvider


@tool
def cls_query(query: str) -> str:
    """Query fake CLS data."""
    return query


@tool
def monitor_query(query: str) -> str:
    """Query fake monitor data."""
    return query


class FakeMCPClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def get_tools(self, *, server_name: str | None = None) -> list:
        assert server_name is not None
        self.calls.append(server_name)
        response = self.responses[server_name]
        if isinstance(response, Exception):
            raise response
        return response


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        dashscope_api_key="",
        mcp_tool_cache_ttl_seconds=60.0,
    )


@pytest.mark.asyncio
async def test_provider_caches_tools_and_tracks_per_server_health() -> None:
    client = FakeMCPClient(
        {
            "cls": [cls_query],
            "monitor": RuntimeError("Authorization: Bearer private-token unavailable"),
        }
    )

    async def client_factory() -> FakeMCPClient:
        return client

    provider = MCPToolProvider(settings=make_settings(), client_factory=client_factory)

    first = await provider.get_tools()
    second = await provider.get_tools()

    assert [item.tool.name for item in first] == ["cls_query"]
    assert second == first
    assert client.calls == ["cls", "monitor"]
    assert provider.health()["cls"].status == "healthy"
    assert provider.health()["monitor"].status == "unavailable"
    assert "private-token" not in (provider.health()["monitor"].error or "")


@pytest.mark.asyncio
async def test_provider_detects_duplicate_tool_names_across_servers() -> None:
    client = FakeMCPClient({"cls": [cls_query], "monitor": [cls_query]})

    async def client_factory() -> FakeMCPClient:
        return client

    provider = MCPToolProvider(settings=make_settings(), client_factory=client_factory)

    with pytest.raises(DuplicateMCPToolError, match="collision"):
        await provider.get_tools()
