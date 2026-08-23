# 二次开发 05：可观测性

## 一条 Trace 的结构

```text
HTTP span
├── agent/graph run
├── model call（model、tokens、latency）
├── retrieval（query、sources、scores）
├── tool call（name、args hash、status）
├── MCP transport
└── checkpoint write
```

所有层传播同一个 `trace_id`，图运行另有 `run_id`，会话使用 `thread_id`。

## 指标

- 请求量、成功率、P50/P95/P99。
- 首 Token 时间、流中断率。
- 模型调用次数、Token 和成本。
- 每个 Tool 的延迟、错误率、重试率。
- Planner 步骤数、Replan 次数、强制终止次数。
- RAG 无结果率和索引任务失败率。
- Checkpoint 读写失败率。

## 日志

使用结构化字段，不靠拼字符串检索。Prompt 和完整工具结果可能含敏感数据，默认只记版本、hash、大小和脱敏摘要；需要全文调试时使用受控采样和短保留期。

## 告警

重点告警：模型鉴权/限流突增、MCP 连续失败、Milvus不可用、SSE 中断率、单任务步骤/成本超限、跨租户过滤缺失。可观测系统本身不能依赖被诊断的同一故障域而毫无冗余。

