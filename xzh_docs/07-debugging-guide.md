# 调试与故障定位手册

## 1. 总原则：先确定坏在哪一层

```text
浏览器
→ HTTP/SSE
→ FastAPI/Pydantic
→ Agent/Graph
→ Model
→ Tool/MCP
→ Milvus/Prometheus/外部系统
```

每次只跨一层验证。不要看到“回答错误”就立刻改 Prompt。

## 2. 启动失败

### 症状：导入 `app.main` 就报 Milvus 连接失败

原因：VectorStore 模块级单例在 FastAPI lifespan 前初始化。检查：

```bash
docker compose -f vector-database.yml ps
nc -vz localhost 19530
tail -n 200 server.log
```

确认 `.env` 中 host/port、容器状态和 Collection Schema。长期修复是移除导入期连接，不是无限增加启动 sleep。

### 症状：API Key 为空

检查启动工作目录是否仓库根目录、`.env` 是否存在、变量名是否为 `DASHSCOPE_API_KEY`。日志只应输出掩码，禁止打印完整 Key。

## 3. HTTP 422

说明通常还未进入路由函数。检查：

- JSON 是否合法。
- `/api/chat` 当前示例字段是 `Id` 和 `Question`。
- `Content-Type: application/json`。
- 必填值是否为 null。

先看响应中的 `loc`，不要查模型日志。

## 4. HTTP 正常但无流式文本

按顺序检查：

1. `curl -N` 是否逐块收到 SSE。
2. 浏览器 Network 中响应是否逐步增长。
3. `query_stream()` 是否收到 `AIMessageChunk`。
4. 文本位于 `content_blocks` 还是 `content`。
5. 反向代理是否启用缓冲。
6. 前端是否识别 `content` 和 `done`。

如果所有文本最后一次出现，多半是某层缓冲；如果完全没有文本但有 complete，多半是消息字段过滤不匹配。

## 5. Agent 不调用工具

验证以下证据：

- 工具是否真的在 `all_tools` 日志中。
- name、description、args schema 是否正确。
- 用户问题是否明确需要实时/私有数据。
- 是否存在名字或功能高度重叠的工具。
- 模型是否支持并正确返回 tool calling。

建立一个明确问题作为基线，例如“调用时间工具告诉我上海当前时间”。若明确问题都不调用，再检查绑定和协议；若只在模糊问题不调用，优先改善描述和 Prompt。

## 6. 工具调用失败

区分：参数校验失败、业务返回失败、网络异常、超时和权限错误。当前不少 Tool 把异常转成普通字符串，日志有 error 但 Agent 仍继续。生产化应给结果增加明确 `status/error_code/retryable`。

对 MCP 错误，使用 `format_exception_chain` 展开 ExceptionGroup。检查 MCP Server 日志、URL、Transport、端口及工具 Schema是否已刷新。

## 7. RAG 没有命中

按写入和读取分开排查：

### 写入

1. 上传文件是否真的保存。
2. 索引日志是否成功，而非仅上传成功。
3. 分片数量是否大于 0。
4. Embedding 维度是否为 1024。
5. Collection 是否存在并加载。
6. metadata `_source` 是否正确。

### 读取

1. 直接调用 Retriever，绕开 Agent。
2. 查看返回 Document 的正文和 source。
3. 确认 L2/score 的方向。
4. 调整 query 表述和 Top K。
5. 再确认 Agent 是否真的调用知识工具。

## 8. AIOps 不结束或过早结束

记录每次：节点名、剩余 plan 数、past_steps 数、response 是否为空、Replanner action。

不结束：检查 `continue` 是否反复返回、Executor 是否真的移除 `plan[0]`、replan 是否反复生成等价步骤、MAX_STEPS 是否生效。

过早结束：检查 Replanner Prompt 的“优先结束”、截断后的历史是否误导模型、工具失败文本是否被误认为已有证据、条件边是否在空 response 时直接 END。

## 9. 会话串线或历史丢失

- 串线：两个用户是否复用了同一 session/thread id。
- 丢失：进程是否重启、请求是否落到不同 Worker、是否调用 clear。
- 历史异常：检查 Checkpoint 原始 messages，而非只看转换后的 API。
- 时间戳不可信：当前历史没有原生 timestamp 时用“读取时当前时间”填充，并非消息实际发生时间。

## 10. 最小诊断信息模板

报告问题时至少保存：

```text
时间与时区：
接口和请求 ID：
session/thread id：
预期：
实际：
HTTP 状态与原始响应：
相关日志（脱敏）：
节点/工具名：
依赖版本：
是否可稳定复现：
最近代码/配置变化：
```

## 11. 建议的调试日志字段

```text
trace_id, run_id, thread_id, user_id, tenant_id,
node, model, prompt_version, tool_name, tool_call_id,
latency_ms, retry_count, token_usage, status, error_code
```

日志不应记录完整 API Key、Cookie、个人敏感信息或未经脱敏的业务日志。

