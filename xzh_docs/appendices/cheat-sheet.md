# 项目命令与 API 速查

## 环境与启动

```bash
uv sync
docker compose -f vector-database.yml up -d
make start
make status-mcp
make logs
```

前台开发：

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 9900 --reload
```

## 请求

```bash
curl -X POST http://localhost:9900/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"Id":"demo","Question":"现在几点？"}'
```

```bash
curl -N -X POST http://localhost:9900/api/chat_stream \
  -H 'Content-Type: application/json' \
  -d '{"Id":"demo","Question":"解释 RAG"}'
```

```bash
curl -N -X POST http://localhost:9900/api/aiops \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"diag-1"}'
```

```bash
curl -F 'file=@aiops-docs/disk_high_usage.md' \
  http://localhost:9900/api/upload
```

## LangChain/LangGraph 常用调用

```python
await runnable.ainvoke(input)
async for item in runnable.astream(input): ...
model.bind_tools(tools)
prompt | model.with_structured_output(Schema)
```

```python
workflow = StateGraph(State)
workflow.add_node("name", node)
workflow.add_edge("a", "b")
workflow.add_conditional_edges("a", route, mapping)
graph = workflow.compile(checkpointer=checkpointer)
```

## 快速定位

```bash
rg 'create_agent|StateGraph|@tool|MemorySaver' app
rg 'EventSourceResponse|stream_mode' app
rg 'Milvus|embed_|as_retriever' app
rg 'MultiServerMCPClient|transport' app mcp_servers
```

查看锁定版本：

```bash
uv tree | rg 'fastapi|langchain|langgraph|milvus|mcp'
```

