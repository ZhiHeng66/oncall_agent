# 源码导读：按运行方向理解仓库

## 1. 总体依赖方向

理想依赖方向是：

```text
API → Service → Agent/Tool → Core/External System
          ↓
        Models
```

API 不应直接操作 Milvus，Tool 不应依赖 FastAPI Request，核心基础设施不应了解前端事件格式。当前项目总体遵循该方向，但模块级单例和交叉 import 使启动依赖较紧。

## 2. 应用入口

### `app/main.py`

负责创建 FastAPI、注册 CORS/Router、挂载静态文件和管理生命周期。阅读时关注：

- `lifespan` 的启动/关闭边界。
- Router prefix 如何组成最终 URL。
- `StaticFiles` 与根路径返回 `index.html`。
- `debug` 如何影响 Uvicorn reload。

### `app/config.py`

所有环境配置的类型化入口。`mcp_servers` 属性把扁平环境变量组合成 Adapter 需要的嵌套字典。新增配置时应同时更新 `.env` 示例、默认值、文档和部署配置。

## 3. API 层

### `app/api/chat.py`

- `/chat`：等待完整 Agent 答案。
- `/chat_stream`：把 Service 事件映射成 SSE。
- `/chat/clear`：删除内存 Checkpoint。
- `/chat/session/{id}`：从 Checkpoint 提取消息。

此文件是协议适配器，不负责决定是否调用工具。

### `app/api/aiops.py`

消费 `aiops_service.diagnose()` 的异步事件；收到 `complete/error` 后结束流。注释中的示例事件要与 Service 实际格式保持同步。

### `app/api/file.py`

验证并保存上传文件，然后同步创建索引。覆盖旧文件采用先删除本地文件再写入；向量层通过 `_source` 删除旧 Chunk。索引失败当前不会使上传 HTTP 失败。

### `app/api/health.py`

健康检查应区分 liveness（进程活着）与 readiness（依赖可用）。维护时确认当前端点具体承诺哪一种。

## 4. 对话 Agent

### `app/services/rag_agent_service.py`

类初始化创建 ChatQwen、默认工具、MemorySaver，但延迟加载 MCP 并创建 Agent。关键方法：

| 方法 | 职责 |
|---|---|
| `_initialize_agent` | 发现 MCP 工具并 `create_agent` |
| `_build_system_prompt` | 定义通用行为边界 |
| `query` | 非流式 `ainvoke` |
| `query_stream` | `messages` 模式流式输出 |
| `get_session_history` | 读取 Checkpoint 并转换前端格式 |
| `clear_session` | 删除 thread |

`trim_messages_middleware` 目前是孤立函数，阅读时不要把“已写”误当“已生效”。

## 5. AIOps 图

### `app/services/aiops_service.py`

负责图结构而非节点内部推理：注册节点、固定边、条件边、编译、流式执行、事件格式化。`diagnose()` 只是用固定 AIOps 任务文本调用通用 `execute()`。

### `app/agent/aiops/state.py`

是图的数据契约。修改字段时必须同时检查：初始状态、所有节点返回值、Reducer、事件格式化、Checkpoint 序列化和测试。

### `planner.py`

四段流程：查经验 → 列工具 → 构造 Prompt → Structured Output。它把工具描述交给模型参考，但不执行这些业务工具。

### `executor.py`

一次处理一个计划步骤。先绑定工具让模型选择，再由 `ToolNode` 执行，最后让模型总结。这里是工具参数、工具消息序列和执行结果进入 State 的关键位置。

### `replanner.py`

控制成本与终止。除模型 action 外还有代码硬限制，所以调试“为何提前结束”时要同时检查 Prompt 决策和 Python 分支。

## 6. 本地工具

### `app/tools/__init__.py`

`DEFAULT_LOCAL_AGENT_TOOLS` 是工具注册中心的雏形。新增 Tool 如果忘记注册，函数即使存在也不会被模型发现。

### `knowledge_tool.py`

从 VectorStore 创建 Retriever，返回格式化上下文和原始 Document。Tool 内捕获所有异常并返回错误字符串，调用者不会收到异常；这影响重试和告警策略。

### `time_tool.py`

展示最简单的纯函数 Tool：参数 Schema 清晰、无外部网络、失败转字符串。

### `query_metrics_alerts.py`

是主应用直接调用 Prometheus 的本地 Tool。阅读时关注 HTTP 超时、查询语句、返回结构和错误语义。

## 7. RAG 服务

| 文件 | 职责 |
|---|---|
| `document_splitter_service.py` | 文本 → `List[Document]` |
| `vector_embedding_service.py` | 文本 → 1024 维向量 |
| `vector_index_service.py` | 文件读取、删旧、分块、写入 |
| `vector_store_manager.py` | LangChain Milvus VectorStore |
| `vector_search_service.py` | PyMilvus 直接搜索的备用封装 |
| `core/milvus_client.py` | 连接、Schema、索引、Collection 生命周期 |

`milvus_client.py` 对 PyMilvus alias 做了运行时 monkey patch，说明项目曾遇到 ORM/Client alias 不一致。升级 PyMilvus 或 langchain-milvus 时要优先回归这部分。

## 8. MCP

### `app/agent/mcp_client.py`

包含五类关注点：单例、Client 创建、工具发现安全包装、重试 interceptor、Transport 配置提示。它没有主动健康检查所有 Server，也没有租户级工具过滤。

### `mcp_servers/*.py`

它们是独立应用，不共享主 FastAPI 进程内存。修改工具 Schema 后，通常需要重启 MCP Server，并让主 Agent 重新加载工具；当前 Agent 初始化一次后会缓存工具列表。

## 9. 前端

`static/app.js` 负责发请求、消费 SSE、按事件类型更新页面。排查“后端日志有输出但页面无内容”时，检查浏览器 Network 中的原始 SSE，再检查 JS 解析和 DOM 更新。

## 10. 阅读一个功能的固定方法

以“知识库问答”为例：

1. 找入口 URL 和请求模型。
2. 找 Service 方法和异步边界。
3. 找 Agent 注册的 Tool。
4. 找 Tool 的外部依赖。
5. 记录每层输入/输出类型。
6. 找异常在哪层被捕获或转成文本。
7. 找状态写到哪里、进程重启是否保留。
8. 用日志或断点证明推断。

这种方法比从第一行逐文件阅读更高效。

