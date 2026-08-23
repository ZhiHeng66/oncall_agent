"""Central tool registry with explicit agent-level permissions."""

from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from app.config import Settings, config
from app.tools.knowledge_tool import retrieve_knowledge
from app.tools.mcp_provider import MCPServerHealth, MCPToolProvider, mcp_tool_provider
from app.tools.query_metrics_alerts import query_prometheus_alerts
from app.tools.time_tool import get_current_time

AgentName = Literal["legacy_chat", "knowledge", "data", "task", "supervisor", "reviewer"]
ToolSource = Literal["local", "mcp"]


class ToolSpec(BaseModel):
    name: str
    description: str
    category: str
    read_only: bool = True
    allowed_agents: set[AgentName] = Field(default_factory=set)
    timeout_seconds: float
    source: ToolSource
    server_name: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    spec: ToolSpec
    tool: BaseTool


class DuplicateToolNameError(ValueError):
    """Raised when local and discovered tools share a name."""


class ToolRegistry:
    """Resolve tools from a finite registry according to agent permissions."""

    def __init__(
        self,
        settings: Settings = config,
        mcp_provider: MCPToolProvider = mcp_tool_provider,
    ) -> None:
        self._settings = settings
        self._mcp_provider = mcp_provider
        self._local: dict[str, RegisteredTool] = {}
        self._mcp: dict[str, RegisteredTool] = {}

    def register_local(
        self,
        tool: BaseTool,
        *,
        category: str,
        allowed_agents: set[AgentName],
        read_only: bool = True,
    ) -> None:
        if tool.name in self._local:
            raise DuplicateToolNameError(f"Duplicate local tool name: {tool.name!r}")
        spec = ToolSpec(
            name=tool.name,
            description=tool.description,
            category=category,
            read_only=read_only,
            allowed_agents=allowed_agents,
            timeout_seconds=self._settings.tool_timeout_seconds,
            source="local",
            input_schema=tool.get_input_schema().model_json_schema(),
        )
        self._local[tool.name] = RegisteredTool(spec=spec, tool=tool)

    async def get_tools_for_agent(
        self,
        agent_name: AgentName,
        *,
        include_mcp: bool = True,
    ) -> list[BaseTool]:
        registered = await self.get_registered_tools_for_agent(
            agent_name,
            include_mcp=include_mcp,
        )
        return [item.tool for item in registered]

    async def get_registered_tools_for_agent(
        self,
        agent_name: AgentName,
        *,
        include_mcp: bool = True,
    ) -> list[RegisteredTool]:
        registered = list(self._local.values())
        if include_mcp and agent_name in {"legacy_chat", "data"}:
            await self._load_mcp_tools()
            registered.extend(self._mcp.values())
        return [item for item in registered if agent_name in item.spec.allowed_agents]

    async def get_tool_descriptions(self, agent_name: AgentName) -> list[ToolSpec]:
        await self.get_tools_for_agent(agent_name)
        registered = [*self._local.values(), *self._mcp.values()]
        return [item.spec for item in registered if agent_name in item.spec.allowed_agents]

    async def refresh_mcp_tools(self) -> list[BaseTool]:
        await self._load_mcp_tools(force_refresh=True)
        return [item.tool for item in self._mcp.values()]

    async def warmup_mcp_tools(self) -> None:
        await self._load_mcp_tools()

    def health(self) -> dict[str, MCPServerHealth]:
        return self._mcp_provider.health()

    async def _load_mcp_tools(self, *, force_refresh: bool = False) -> None:
        discovered = await self._mcp_provider.get_tools(force_refresh=force_refresh)
        mcp_tools: dict[str, RegisteredTool] = {}
        for item in discovered:
            name = item.tool.name
            if name in self._local or name in mcp_tools:
                raise DuplicateToolNameError(f"Tool name collision: {name!r}")
            spec = ToolSpec(
                name=name,
                description=item.tool.description,
                category="external_data",
                allowed_agents={"legacy_chat", "data"},
                timeout_seconds=self._settings.tool_timeout_seconds,
                source="mcp",
                server_name=item.server_name,
                input_schema=item.tool.get_input_schema().model_json_schema(),
            )
            mcp_tools[name] = RegisteredTool(spec=spec, tool=item.tool)
        self._mcp = mcp_tools


def create_default_tool_registry(
    mcp_provider: MCPToolProvider = mcp_tool_provider,
) -> ToolRegistry:
    registry = ToolRegistry(mcp_provider=mcp_provider)
    registry.register_local(
        retrieve_knowledge,
        category="knowledge",
        allowed_agents={"legacy_chat", "knowledge"},
    )
    registry.register_local(
        get_current_time,
        category="utility",
        allowed_agents={"legacy_chat", "data"},
    )
    registry.register_local(
        query_prometheus_alerts,
        category="external_data",
        allowed_agents={"legacy_chat", "data"},
    )
    return registry


tool_registry = create_default_tool_registry()
