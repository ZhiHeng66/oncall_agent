import pytest

from app.config import Settings
from app.tools.mcp_provider import MCPToolProvider
from app.tools.registry import ToolRegistry


class UnavailableMCPClient:
    async def get_tools(self, *, server_name: str | None = None) -> list:
        raise ConnectionError(f"{server_name} is unavailable")


@pytest.mark.asyncio
async def test_all_mcp_servers_unavailable_does_not_block_local_knowledge_tool() -> None:
    settings = Settings(_env_file=None, dashscope_api_key="")

    async def client_factory() -> UnavailableMCPClient:
        return UnavailableMCPClient()

    provider = MCPToolProvider(settings=settings, client_factory=client_factory)
    registry = ToolRegistry(settings=settings, mcp_provider=provider)

    from app.tools.knowledge_tool import retrieve_knowledge

    registry.register_local(
        retrieve_knowledge,
        category="knowledge",
        allowed_agents={"knowledge"},
    )

    assert await registry.get_tools_for_agent("data") == []
    knowledge_tools = await registry.get_tools_for_agent("knowledge")
    assert [item.name for item in knowledge_tools] == ["retrieve_knowledge"]
    assert all(status.status == "unavailable" for status in registry.health().values())


@pytest.mark.asyncio
async def test_mcp_client_creation_failure_is_cached_as_unavailable() -> None:
    settings = Settings(_env_file=None, dashscope_api_key="")

    async def failing_client_factory() -> UnavailableMCPClient:
        raise RuntimeError("api_key=private-value invalid client configuration")

    provider = MCPToolProvider(settings=settings, client_factory=failing_client_factory)

    assert await provider.get_tools() == []
    assert all(status.status == "unavailable" for status in provider.health().values())
    assert all("private-value" not in (status.error or "") for status in provider.health().values())
