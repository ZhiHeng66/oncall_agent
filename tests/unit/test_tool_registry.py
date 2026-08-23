from typing import Any

import pytest
from langchain_core.tools import tool

from app.config import Settings
from app.tools.mcp_provider import DiscoveredMCPTool, MCPServerHealth
from app.tools.registry import DuplicateToolNameError, ToolRegistry


@tool
def knowledge_lookup(query: str) -> str:
    """Retrieve fake knowledge."""
    return query


@tool
def external_lookup(query: str) -> str:
    """Retrieve fake external data."""
    return query


class FakeProvider:
    def __init__(self, tools: list[DiscoveredMCPTool]) -> None:
        self.tools = tools

    async def get_tools(self, *, force_refresh: bool = False) -> list[DiscoveredMCPTool]:
        return self.tools

    def health(self) -> dict[str, MCPServerHealth]:
        return {}


def make_registry(provider: Any) -> ToolRegistry:
    return ToolRegistry(
        settings=Settings(_env_file=None, dashscope_api_key=""),
        mcp_provider=provider,
    )


@pytest.mark.asyncio
async def test_registry_enforces_agent_tool_permissions() -> None:
    provider = FakeProvider([DiscoveredMCPTool(external_lookup, "external")])
    registry = make_registry(provider)
    registry.register_local(
        knowledge_lookup,
        category="knowledge",
        allowed_agents={"legacy_chat", "knowledge", "task"},
    )

    knowledge_tools = await registry.get_tools_for_agent("knowledge")
    data_tools = await registry.get_tools_for_agent("data")
    supervisor_tools = await registry.get_tools_for_agent("supervisor")

    assert [item.name for item in knowledge_tools] == ["knowledge_lookup"]
    assert [item.name for item in data_tools] == ["external_lookup"]
    assert supervisor_tools == []


@pytest.mark.asyncio
async def test_registry_detects_local_and_mcp_name_collision() -> None:
    provider = FakeProvider([DiscoveredMCPTool(knowledge_lookup, "external")])
    registry = make_registry(provider)
    registry.register_local(
        knowledge_lookup,
        category="knowledge",
        allowed_agents={"knowledge"},
    )

    with pytest.raises(DuplicateToolNameError, match="collision"):
        await registry.get_tools_for_agent("data")
