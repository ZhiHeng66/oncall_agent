# 依赖地图与版本基线

## 1. 为什么以锁文件为准

`pyproject.toml` 中的 `langchain>=0.1.0` 表示允许安装的下限，不代表当前运行版本。`uv.lock` 才描述这次环境解析出的确定版本。LangChain 和 LangGraph 在 0.x 到 1.x 之间变化很大，阅读旧教程时尤其要警惕 API 差异。

当前关键锁定版本：

| 依赖 | 锁定版本 | 项目用途 |
|---|---:|---|
| Python | 3.11～3.13 | 运行语言，项目声明 `>=3.11,<3.14` |
| FastAPI | 0.128.8 | API、生命周期、路由、静态文件 |
| Pydantic | 2.12.5 | 请求模型、结构化输出、配置基础 |
| LangChain | 1.2.10 | Agent、消息、Prompt、Tool、Runnable |
| LangGraph | 1.0.8 | 状态图、Checkpoint、流式事件 |
| langchain-qwq | 0.3.4 | ChatQwen 模型集成 |
| langchain-mcp-adapters | 0.2.1 | MCP Tool 转 LangChain Tool |
| FastMCP | 2.14.5 | MCP Server |
| PyMilvus | 2.6.9 | Milvus 原生连接和 Collection |
| langchain-milvus | 0.3.3 | LangChain VectorStore |
| sse-starlette | 项目锁定版本 | SSE 响应 |

## 2. 分层依赖

```text
业务代码
├── FastAPI / Pydantic / sse-starlette
├── LangChain
│   ├── langchain-core
│   ├── langchain-qwq
│   ├── langchain-milvus
│   └── langchain-mcp-adapters
├── LangGraph
├── DashScope / OpenAI-compatible API
├── PyMilvus
└── FastMCP
```

LangChain 提供组件协议和 Agent 封装，LangGraph负责有状态执行；二者互补而非替代。PyMilvus 是数据库原生 SDK，`langchain-milvus` 则把它包装为 LangChain VectorStore。

## 3. 升级检查清单

升级任何关键依赖前：

1. 新建分支并保存旧 `uv.lock`。
2. 检查 `create_agent`、`ToolNode`、`MemorySaver`、`astream` 的变更。
3. 检查 ChatQwen 的 `content`、`content_blocks` 和 tool call 格式。
4. 检查 MCP transport 名称与 interceptor 签名。
5. 检查 Milvus Collection Schema、JSON 查询表达式和连接 alias。
6. 跑普通聊天、流式聊天、上传检索、AIOps 四条冒烟链路。
7. 对比 SSE 事件、工具参数和最终状态，而不只看 HTTP 200。

## 4. 当前依赖声明的改进建议

生产项目不宜只用过宽的最低版本约束。可以使用兼容范围并继续提交锁文件，例如限制 LangChain/LangGraph 的主版本。升级应由回归测试驱动，不能只依据“安装成功”。

