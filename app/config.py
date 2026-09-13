"""配置管理模块

使用 Pydantic Settings 实现类型安全的配置管理
"""

import os
from typing import Any, Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = None if os.getenv("ONCALL_DISABLE_ENV_FILE") == "1" else ".env"


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用配置
    app_name: str = "Enterprise Knowledge Multi-Agent Platform"
    app_version: str = "2.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 9900

    # DashScope 配置
    dashscope_api_key: str = ""  # 默认空字符串，实际使用需从环境变量加载
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_model: str = "qwen-max"
    dashscope_embedding_model: str = "text-embedding-v4"  # v4 支持多种维度（默认 1024）

    # Milvus 配置
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_timeout: int = 10000  # 毫秒

    # RAG 配置
    rag_top_k: int = 3
    knowledge_min_relevance_score: float = 0.3
    rag_model: str = "qwen-max"  # 使用快速响应模型，不带扩展思考
    supervisor_model: str = "qwen-max"
    specialist_model: str = "qwen-max"
    reviewer_model: str = "qwen-max"
    chat_temperature: float = 0.7
    agent_temperature: float = 0.0
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2

    # 文档分块配置
    chunk_max_size: int = 800
    chunk_overlap: int = 100

    # MCP 服务配置（transport: stdio | sse | streamable-http）
    # 腾讯云托管 MCP 的 URL 通常含 /sse/，需使用 sse；本地 FastMCP 使用 streamable-http
    mcp_cls_transport: str = "streamable-http"
    mcp_cls_url: str = "http://localhost:8003/mcp"
    mcp_monitor_transport: str = "streamable-http"
    mcp_monitor_url: str = "http://localhost:8004/mcp"
    mcp_ticket_transport: str = "streamable-http"
    mcp_ticket_url: str = "http://localhost:8005/mcp"
    mcp_tool_cache_ttl_seconds: float = 300.0

    # Agent 运行限制
    checkpointer_backend: Literal["memory", "sqlite", "postgres"] = "sqlite"
    checkpoint_path: str = "data/multi_agent.sqlite3"
    database_url: str = ""
    postgres_pool_min_size: int = Field(default=1, ge=0)
    postgres_pool_max_size: int = Field(default=10, ge=1)
    postgres_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    supervisor_max_rounds: int = 4
    task_max_steps: int = 8
    reviewer_max_retries: int = 1
    agent_timeout_seconds: float = 90.0
    tool_timeout_seconds: float = 15.0
    tool_max_retries: int = 3
    data_max_tool_calls: int = 4
    tool_result_max_chars: int = 4000
    api_source_excerpt_max_chars: int = 500
    api_event_data_max_chars: int = 2000
    api_answer_max_chars: int = 20000
    graph_recursion_limit: int = 50

    # Prometheus
    prometheus_base_url: str = "http://127.0.0.1:9090"
    prometheus_request_timeout: float = 10.0

    @model_validator(mode="after")
    def validate_persistence(self) -> "Settings":
        if self.checkpointer_backend == "postgres" and not self.database_url.strip():
            raise ValueError("DATABASE_URL is required for the postgres persistence backend")
        if self.postgres_pool_max_size < self.postgres_pool_min_size:
            raise ValueError("POSTGRES_POOL_MAX_SIZE must be at least POSTGRES_POOL_MIN_SIZE")
        return self

    @property
    def mcp_servers(self) -> dict[str, dict[str, Any]]:
        """获取完整的 MCP 服务器配置"""
        return {
            "cls": {
                "transport": self.mcp_cls_transport,
                "url": self.mcp_cls_url,
            },
            "monitor": {
                "transport": self.mcp_monitor_transport,
                "url": self.mcp_monitor_url,
            },
            "ticket": {
                "transport": self.mcp_ticket_transport,
                "url": self.mcp_ticket_url,
            },
        }


# 全局配置实例
config = Settings()
