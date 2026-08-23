# 项目全景：先建立地图，再进入细节

## 1. 项目解决什么问题

SuperBizAgent 同时提供三类能力：

1. 通用对话：模型根据问题直接回答，或者选择本地/MCP 工具。
2. 知识库问答：上传 Markdown/TXT，切分并存入 Milvus，Agent 在需要时检索。
3. AIOps 诊断：先规划步骤，再逐步调用监控、日志、知识库等工具，过程中重规划，最后生成报告。

它不是一个单一 Agent，而是两个运行模型共存：

- `RagAgentService` 使用 LangChain 的 `create_agent()`，工具循环由框架封装。
- `AIOpsService` 显式构建 LangGraph，节点是 Planner、Executor 和 Replanner。

## 2. 系统组件图

```text
Browser / API Client
        │ HTTP + SSE
        ▼
┌──────────────── FastAPI ────────────────┐
│ /api/chat  /api/chat_stream /api/aiops │
│ /api/upload /api/health                │
└──────────┬───────────────┬─────────────┘
           │               │
           ▼               ▼
   RagAgentService      AIOpsService
   create_agent()       StateGraph
           │               │
           ├───────────────┤
           ▼               ▼
      ChatQwen / DashScope LLM
           │
      ┌────┴────────────────────┐
      ▼                         ▼
 Local LangChain Tools     MCP Remote Tools
 time / RAG / alerts       CLS / Monitor
      │                         │
      ▼                         ▼
 Milvus + Embedding       FastMCP Servers
```

## 3. 目录职责

| 目录 | 职责 | 阅读重点 |
|---|---|---|
| `app/api` | HTTP 边界、请求解析、SSE 编码 | 不放 Agent 决策 |
| `app/services` | 业务编排、RAG、索引和工作流 | 两条主链路集中处 |
| `app/agent` | MCP 接入和 AIOps 节点 | Agent 的显式决策逻辑 |
| `app/tools` | 可被模型调用的本地工具 | Schema、描述、返回值 |
| `app/core` | LLM 和 Milvus 基础设施 | 连接、模型工厂、集合结构 |
| `app/models` | Pydantic API 模型 | 输入校验与 OpenAPI |
| `mcp_servers` | 独立的远程工具服务 | Transport 和工具暴露 |
| `static` | 原生 HTML/CSS/JS 前端 | SSE 消费和页面状态 |

## 4. 对话链路

普通请求：

```text
POST /api/chat
→ ChatRequest 校验并把 Id/Question 映射为 id/question
→ rag_agent_service.query(question, session_id)
→ 首次请求时加载 MCP tools 并 create_agent
→ agent.ainvoke(messages, thread_id)
→ 模型可能调用工具
→ Agent 循环执行工具并再次调用模型
→ 返回最后一条消息
→ FastAPI 包装 JSON
```

流式请求与普通请求共用 Agent，但调用 `agent.astream(..., stream_mode="messages")`。服务只把 AI 文本块转换为内部事件，API 层再编码为 SSE：

```text
AIMessageChunk
→ {"type": "content", "data": "..."}
→ event: message\ndata: {...}\n\n
→ 浏览器逐块追加文本
```

需要注意：`query_stream()` 当前主要输出文本和完成/错误事件，API 层虽然兼容 `tool_call`、`search_results` 等类型，但服务目前未完整产出这些事件。

## 5. RAG 的写入与读取

写入链路是确定性的：

```text
POST /api/upload
→ 保存到 uploads
→ VectorIndexService.index_single_file
→ 删除相同 _source 的旧数据
→ MarkdownHeaderTextSplitter
→ RecursiveCharacterTextSplitter
→ DashScope Embedding
→ LangChain Milvus.add_documents
```

对话读取链路不是“每次必查”：`retrieve_knowledge` 被注册成一个 Tool，模型依据工具描述决定是否调用。AIOps Planner 则在制定计划前显式执行 `retrieve_knowledge.ainvoke()`，因此两条 RAG 路径行为不同。

## 6. AIOps 状态图

```text
START
  │
  ▼
Planner：知识检索 + 工具清单 + 结构化计划
  │
  ▼
Executor：取 plan[0]，让模型选工具并执行
  │
  ▼
Replanner：continue / replan / respond
  │
  ├── 有必要继续 ───────────────▶ Executor
  └── response 已生成或结束 ───▶ END
```

核心状态：

```python
class PlanExecuteState(TypedDict):
    input: str
    plan: list[str]
    past_steps: Annotated[list[tuple], operator.add]
    response: str
```

`plan` 和 `response` 默认覆盖，`past_steps` 通过 Reducer 追加。节点只需返回要更新的字段。

## 7. 四个外部边界

阅读时要时刻辨认代码是否跨越外部边界：

1. DashScope：ChatQwen 和 OpenAI 兼容 Embedding 请求。
2. Milvus：文档向量和元数据持久化。
3. MCP Server：HTTP Transport 上的日志/监控工具。
4. Browser：SSE 长连接及前端渲染。

跨边界意味着需要特别处理超时、重试、鉴权、序列化、错误降级和可观测性。

## 8. 当前实现中容易误判的点

- `MemorySaver` 只在进程内保存 Checkpoint，重启丢失，也不等于用户长期记忆。
- `trim_messages_middleware()` 已定义，但没有传给 `create_agent()`，不能假设历史已自动裁剪。
- `vector_search_service.py` 是直接使用 PyMilvus 的另一条搜索封装；主知识工具实际走 `VectorStore.as_retriever()`。
- `LLMFactory` 存在，但当前 Agent 节点主要直接实例化 `ChatQwen`。
- `Executor` 当前处理一次模型工具决策；如果工具结果引发第二轮新工具调用，它不会像完整 ReAct 循环那样持续执行。
- API 文档中的部分事件类型比服务当前真正产生的类型更丰富，调试时应看运行数据而非只看注释。

## 9. 第一次阅读源码的顺序

按执行方向阅读，而不是按目录字母顺序：

1. `app/main.py`
2. `app/api/chat.py` 与 `app/api/aiops.py`
3. `app/services/rag_agent_service.py`
4. `app/services/aiops_service.py`
5. `app/agent/aiops/state.py`、`planner.py`、`executor.py`、`replanner.py`
6. `app/tools/*`
7. `app/services/vector_*` 与 `document_splitter_service.py`
8. `app/agent/mcp_client.py` 与 `mcp_servers/*`

读完后，不看本文尝试重新画出组件图和两条调用链。能画对，才进入第 1 天。

