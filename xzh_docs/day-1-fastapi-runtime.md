# 第 1 天：FastAPI、异步运行时与 SSE

## 今日目标

今天不碰复杂 Agent 决策，先掌握系统外壳。完成后你应能从浏览器或 `curl` 发出请求，一路跟到 Service，并解释普通响应和流式响应为什么不同。

## 1. 从启动命令理解入口

常用启动命令：

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 9900
```

`app.main:app` 的含义是：导入 `app/main.py` 模块，取得名为 `app` 的 FastAPI 对象。导入不是无副作用的：Python 会递归执行模块级 import 和全局单例初始化。当前项目的向量服务会在模块导入阶段尝试连接 Milvus，所以有时服务甚至还未进入 lifespan 就因 Milvus 不可用而失败。

### Uvicorn、FastAPI、业务代码的关系

```text
Uvicorn：ASGI Server，监听端口、管理连接
FastAPI：ASGI Application，路由、校验、响应
业务 Service：真正执行 Agent、RAG 和工作流
```

FastAPI 不是 Web Server 本身。Uvicorn 接收网络事件，把标准 ASGI 消息交给 FastAPI。

## 2. 应用生命周期

`app/main.py` 使用 `@asynccontextmanager` 定义 lifespan：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    milvus_manager.connect()
    yield
    milvus_manager.close()
```

`yield` 前是启动逻辑，`yield` 后是关闭逻辑。适合管理数据库连接池、模型客户端、后台任务等进程级资源。

当前项目有一个值得观察的细节：`VectorStoreManager` 是模块级单例，其构造函数会提前连接 Milvus。这说明“连接只发生在 lifespan”并不完全成立。二次开发时应减少导入副作用，把外部连接统一移入生命周期或显式容器。

## 3. Router 如何组合 API

主应用通过 `include_router` 组合模块：

```python
app.include_router(chat.router, prefix="/api", tags=["对话"])
```

而 `app/api/chat.py` 内定义：

```python
router = APIRouter()

@router.post("/chat")
async def chat(request: ChatRequest):
    ...
```

最终路径是前缀 `/api` 加路由 `/chat`，即 `/api/chat`。这种拆法让 API 层只负责协议边界，业务应下沉至 Service。

## 4. Pydantic 请求校验

`ChatRequest` 把外部 JSON 的大写字段映射为 Python 风格字段：

```python
class ChatRequest(BaseModel):
    id: str = Field(..., alias="Id")
    question: str = Field(..., alias="Question")
```

请求：

```json
{"Id": "session-123", "Question": "现在几点？"}
```

函数内部访问 `request.id` 和 `request.question`。FastAPI 在进入函数前完成 JSON 解析、类型校验和模型构造；校验失败会直接返回 422。

动手验证：

```bash
curl -i -X POST http://localhost:9900/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"Id": 123, "Question": null}'
```

观察错误位置 `loc`、错误类型 `type` 和提示 `msg`。API 调试时，422 通常是边界模型问题，还没进入 Agent。

## 5. `async/await` 到底解决什么

聊天函数是 `async def`：

```python
answer = await rag_agent_service.query(...)
```

`await` 让当前协程在等待网络 I/O 时把执行权交还事件循环，使同一进程能处理其他连接。它不是自动创建线程，也不会让同步阻塞代码变快。

本项目中需要特别注意：文档读取、Embedding 和部分 Milvus 操作是同步方法。如果在 `async` 路由中直接执行耗时同步调用，它仍可能阻塞事件循环。生产化可考虑线程池、异步客户端或任务队列。

## 6. 普通响应链路

```text
Client
→ POST /api/chat
→ ChatRequest
→ await RagAgentService.query
→ await agent.ainvoke
→ 得到完整消息列表
→ 提取最后一条消息
→ 返回 dict
→ FastAPI 序列化 JSON
```

当前接口捕获业务异常并返回 HTTP 200 风格的业务 JSON，其中 `code` 可能是 500。这与真正使用 HTTP 500 不同。调用方必须同时检查 HTTP 状态和业务 `code`。二次开发时建议建立统一异常处理策略。

## 7. SSE 流式响应

SSE 是单向的服务端推送协议。响应不会一次结束，而是持续发送：

```text
event: message
data: {"type":"content","data":"你"}

event: message
data: {"type":"content","data":"好"}

event: message
data: {"type":"done","data":null}

```

空行表示一个事件结束。`EventSourceResponse` 接收异步生成器：

```python
async def event_generator():
    async for chunk in rag_agent_service.query_stream(...):
        yield {"event": "message", "data": json.dumps(chunk)}
```

这里存在三层“流”：

1. 模型供应商返回 Token/Chunk。
2. LangGraph `astream` 返回消息及 metadata。
3. FastAPI 把内部事件编码成 SSE 帧。

任意一层缓冲，都可能造成“接口最后能返回，但页面不逐字显示”。

测试 SSE 时使用：

```bash
curl -N -X POST http://localhost:9900/api/chat_stream \
  -H 'Content-Type: application/json' \
  -d '{"Id":"learn-day1","Question":"简要解释 SSE"}'
```

`-N` 禁用 curl 输出缓冲。

## 8. 前后端事件契约

当前对话流内部事件和对外事件有一次转换：

| Service 事件 | API SSE 中的类型 | 含义 |
|---|---|---|
| `content` | `content` | 文本增量 |
| `complete` | `done` | 流结束 |
| `error` | `error` | 执行错误 |
| `tool_call` | `tool_call` | API 已兼容，Service 暂未完整产出 |

事件名、JSON 字段和结束信号组成接口契约。修改任意一侧都要同步检查 `static/app.js`。

## 9. 配置系统

`Settings` 使用 `pydantic-settings` 从环境变量和 `.env` 读取配置：

```text
代码默认值
   ↑ 被 .env 覆盖
   ↑ 被进程环境变量覆盖
```

关键原则：

- API Key 不写进代码或日志。
- 配置字符串进入业务前转成明确类型。
- 开发、测试、生产使用不同环境配置。
- URL 和 Transport 要成对校验。

## 10. 今日实验

### 实验 A：画出请求时序

给 `/api/chat` 的 API 函数、Service.query 入口和退出分别加临时日志，使用同一个 `session_id`，根据日志写出函数调用顺序。

### 实验 B：观察流式时间

在消费每个 chunk 时记录时间戳，对比：

- 首字时间（TTFT）
- 相邻 chunk 时间
- 总耗时

### 实验 C：新增简单接口

新增 `/api/runtime-info`，返回应用版本、当前时间和是否 debug。要求定义响应模型，而不是直接返回任意字典。

### 实验 D：制造边界错误

分别测试：无 `Content-Type`、错误字段名、空 session、服务执行异常。记录 HTTP 状态、响应体和日志位置。

## 11. 今日自测

1. Uvicorn 和 FastAPI 各自负责什么？
2. 为什么 `async def` 中调用同步慢函数仍会阻塞？
3. FastAPI 在什么时候把 JSON 转成 `ChatRequest`？
4. SSE 的事件边界是什么？
5. 为什么流式输出要检查模型、LangGraph、API 三层？
6. 当前 Milvus 连接是否只发生在 lifespan？

能够不看源码画出 `/api/chat_stream` 时序图，即完成第 1 天。

