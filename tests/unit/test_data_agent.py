from typing import Any

from langchain_core.tools import tool

from app.agent.contracts import AgentTask
from app.agent.specialists.data.agent import DataAgent
from app.agent.specialists.data.schemas import DataToolCall, DataToolPlan
from app.config import Settings
from app.tools.mcp_provider import DiscoveredMCPTool
from app.tools.registry import ToolRegistry, ToolSpec


@tool
def successful_query(query: str) -> dict[str, Any]:
    """Return fake external data."""
    return {"value": 42, "token": "must-not-leak", "query": query}


@tool
def failing_query(query: str) -> str:
    """Raise a fake external failure."""
    raise ConnectionError(f"source unavailable for {query}")


@tool
def destructive_query(query: str) -> str:
    """Pretend to mutate external state."""
    return query


@tool
async def slow_query(query: str) -> str:
    """Return too slowly for the configured timeout."""
    import asyncio

    await asyncio.sleep(0.02)
    return query


class EmptyProvider:
    async def get_tools(self, *, force_refresh: bool = False) -> list:
        return []

    def health(self) -> dict:
        return {}


class StaticProvider(EmptyProvider):
    async def get_tools(self, *, force_refresh: bool = False) -> list[DiscoveredMCPTool]:
        return [DiscoveredMCPTool(successful_query, "orders")]


class FakePlanner:
    def __init__(self, calls: list[DataToolCall]) -> None:
        self.calls = calls

    async def plan(self, task: AgentTask, tools: list[ToolSpec]) -> DataToolPlan:
        return DataToolPlan(calls=self.calls)


def make_registry() -> ToolRegistry:
    settings = Settings(_env_file=None, dashscope_api_key="")
    registry = ToolRegistry(settings=settings, mcp_provider=EmptyProvider())  # type: ignore[arg-type]
    for item in (successful_query, failing_query):
        registry.register_local(
            item,
            category="external_data",
            allowed_agents={"data"},
        )
    return registry


def make_task() -> AgentTask:
    return AgentTask(task_id="data-1", objective="查询外部数据")


async def test_data_agent_sanitizes_successful_tool_evidence() -> None:
    planner = FakePlanner([DataToolCall(tool_name="successful_query", arguments={"query": "x"})])
    agent = DataAgent(
        registry=make_registry(),
        planner=planner,
        settings=Settings(_env_file=None, dashscope_api_key=""),
    )

    result = await agent.run(make_task())

    assert result.status == "success"
    assert result.evidence[0].source_type == "tool"
    assert "must-not-leak" not in result.evidence[0].content
    assert "[REDACTED]" in result.evidence[0].content


async def test_data_agent_emits_safe_tool_lifecycle_events() -> None:
    planner = FakePlanner(
        [DataToolCall(tool_name="successful_query", arguments={"query": "private-input"})]
    )
    agent = DataAgent(registry=make_registry(), planner=planner)
    events: list[dict[str, Any]] = []

    result = await agent.run(make_task(), event_sink=events.append)

    assert result.status == "success"
    assert [event["type"] for event in events] == ["tool_started", "tool_completed"]
    assert events[0]["data"] == {
        "tool_name": "successful_query",
        "server_name": None,
        "status": "running",
    }
    assert events[1]["data"]["status"] == "success"
    assert events[1]["data"]["duration_ms"] >= 0
    assert "private-input" not in str(events)


async def test_data_agent_returns_partial_when_one_tool_fails() -> None:
    planner = FakePlanner(
        [
            DataToolCall(tool_name="successful_query", arguments={"query": "x"}),
            DataToolCall(tool_name="failing_query", arguments={"query": "x"}),
        ]
    )
    agent = DataAgent(registry=make_registry(), planner=planner)

    result = await agent.run(make_task())

    assert result.status == "partial"
    assert len(result.evidence) == 1
    assert len(result.errors) == 1
    assert "调用失败" in result.errors[0]


async def test_data_agent_rejects_unregistered_tool_name() -> None:
    planner = FakePlanner([DataToolCall(tool_name="write_database", arguments={})])
    agent = DataAgent(registry=make_registry(), planner=planner)

    result = await agent.run(make_task())

    assert result.status == "failed"
    assert result.evidence == []
    assert result.errors == ["拒绝未授权工具: write_database"]


async def test_data_agent_refuses_registered_non_read_only_tool() -> None:
    registry = make_registry()
    registry.register_local(
        destructive_query,
        category="external_data",
        allowed_agents={"data"},
        read_only=False,
    )
    planner = FakePlanner([DataToolCall(tool_name="destructive_query", arguments={"query": "x"})])

    result = await DataAgent(registry=registry, planner=planner).run(
        AgentTask(
            task_id="write-attempt",
            objective="mutate data",
            preferred_tools=["destructive_query"],
        )
    )

    assert result.status == "failed"
    assert result.evidence == []
    assert "只读数据工具" in result.errors[0]


async def test_data_agent_preserves_mcp_server_and_tool_provenance() -> None:
    settings = Settings(_env_file=None, dashscope_api_key="")
    registry = ToolRegistry(settings=settings, mcp_provider=StaticProvider())  # type: ignore[arg-type]
    planner = FakePlanner(
        [DataToolCall(tool_name="successful_query", arguments={"query": "order-1"})]
    )

    result = await DataAgent(registry=registry, planner=planner, settings=settings).run(make_task())

    assert result.status == "success"
    assert result.evidence[0].source_type == "mcp"
    assert result.evidence[0].source_name == "orders"
    assert result.evidence[0].metadata["tool_name"] == "successful_query"


async def test_data_agent_enforces_per_tool_timeout() -> None:
    settings = Settings(
        _env_file=None,
        dashscope_api_key="",
        tool_timeout_seconds=0.001,
    )
    registry = ToolRegistry(settings=settings, mcp_provider=EmptyProvider())  # type: ignore[arg-type]
    registry.register_local(
        slow_query,
        category="external_data",
        allowed_agents={"data"},
    )
    planner = FakePlanner([DataToolCall(tool_name="slow_query", arguments={"query": "x"})])

    result = await DataAgent(registry=registry, planner=planner, settings=settings).run(make_task())

    assert result.status == "failed"
    assert "调用失败" in result.errors[0]
