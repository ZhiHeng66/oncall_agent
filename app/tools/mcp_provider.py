"""Cached, server-aware discovery of read-only MCP tools."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Literal, Protocol

from langchain_core.tools import BaseTool
from loguru import logger

from app.agent.mcp_client import get_mcp_client_with_retry, sanitize_error_message
from app.config import Settings, config


class MCPClient(Protocol):
    async def get_tools(self, *, server_name: str | None = None) -> list[BaseTool]: ...


@dataclass(frozen=True, slots=True)
class DiscoveredMCPTool:
    tool: BaseTool
    server_name: str


@dataclass(frozen=True, slots=True)
class MCPServerHealth:
    server_name: str
    status: Literal["unknown", "healthy", "unavailable"]
    tool_count: int = 0
    error: str | None = None


class DuplicateMCPToolError(ValueError):
    """Raised when two MCP servers expose the same unqualified tool name."""


class MCPToolProvider:
    """Discover MCP tools per server and cache successful partial results."""

    def __init__(
        self,
        settings: Settings = config,
        client_factory: Callable[[], Awaitable[MCPClient]] = get_mcp_client_with_retry,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._clock = clock
        self._cache: list[DiscoveredMCPTool] | None = None
        self._cache_expires_at = 0.0
        self._health = {
            name: MCPServerHealth(server_name=name, status="unknown")
            for name in settings.mcp_servers
        }
        self._lock = asyncio.Lock()

    async def get_tools(self, *, force_refresh: bool = False) -> list[DiscoveredMCPTool]:
        now = self._clock()
        if not force_refresh and self._cache is not None and now < self._cache_expires_at:
            return list(self._cache)

        async with self._lock:
            now = self._clock()
            if not force_refresh and self._cache is not None and now < self._cache_expires_at:
                return list(self._cache)

            try:
                client = await self._client_factory()
            except Exception as exc:
                public_error = sanitize_error_message(str(exc))
                logger.warning("MCP 客户端创建失败: {}", public_error)
                self._health = {
                    name: MCPServerHealth(
                        server_name=name,
                        status="unavailable",
                        error=public_error,
                    )
                    for name in self._settings.mcp_servers
                }
                self._cache = []
                self._cache_expires_at = now + self._settings.mcp_tool_cache_ttl_seconds
                return []

            discovered: list[DiscoveredMCPTool] = []
            health: dict[str, MCPServerHealth] = {}
            for server_name in self._settings.mcp_servers:
                try:
                    tools = await client.get_tools(server_name=server_name)
                except Exception as exc:
                    public_error = sanitize_error_message(str(exc))
                    logger.warning("MCP Server [{}] 工具发现失败: {}", server_name, public_error)
                    health[server_name] = MCPServerHealth(
                        server_name=server_name,
                        status="unavailable",
                        error=public_error,
                    )
                    continue

                health[server_name] = MCPServerHealth(
                    server_name=server_name,
                    status="healthy",
                    tool_count=len(tools),
                )
                discovered.extend(
                    DiscoveredMCPTool(tool=tool, server_name=server_name) for tool in tools
                )

            self._assert_unique_names(discovered)
            self._health = health
            self._cache = discovered
            self._cache_expires_at = now + self._settings.mcp_tool_cache_ttl_seconds
            return list(discovered)

    async def refresh(self) -> list[DiscoveredMCPTool]:
        return await self.get_tools(force_refresh=True)

    def health(self) -> dict[str, MCPServerHealth]:
        return dict(self._health)

    def clear_cache(self) -> None:
        self._cache = None
        self._cache_expires_at = 0.0

    @staticmethod
    def _assert_unique_names(tools: list[DiscoveredMCPTool]) -> None:
        owners: dict[str, str] = {}
        for discovered in tools:
            name = discovered.tool.name
            if name in owners:
                raise DuplicateMCPToolError(
                    f"MCP tool name collision: {name!r} from "
                    f"{owners[name]!r} and {discovered.server_name!r}"
                )
            owners[name] = discovered.server_name


mcp_tool_provider = MCPToolProvider()
