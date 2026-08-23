# M1 可测试基础层实施交接

> 完成时间：2026-08-23 15:50 CST  
> 分支：`multi-agents`  
> 范围：阶段 0（测试基线）与阶段 1（统一基础设施）

## 1. 完成内容

- 建立 `tests/unit`、`tests/integration`、`tests/contract` 和 `tests/evals` 分层。
- 使用 Fake LLM、Fake Retriever 和 Fake MCP Client，普通测试不访问模型服务、Milvus
  或在线 MCP Server。
- 为旧 `/api/chat`、`/api/chat_stream`、`/api/aiops` 建立最小 JSON/SSE 契约测试。
- 保存 15 条无预设成绩的初始代表性请求，供后续 Eval 扩展。
- 将聊天服务以及 Planner、Executor、Replanner 的模型创建统一迁移到
  `app/core/llm_factory.py`，支持角色配置和 Fake 注入。
- 新增中央 Tool Registry，定义 `legacy_chat`、`knowledge`、`data`、`task`、
  `supervisor`、`reviewer` 的工具权限。Supervisor 和 Reviewer 当前权限为空。
- 新增 MCP Tool Provider：按 Server 独立发现、TTL 缓存、健康状态、工具来源、
  重名检测、部分失败降级和错误清洗。
- MCP 工具调用使用配置化超时、有限重试和指数退避。
- 新增运行限制与 Checkpointer Factory；当前仅支持开发环境 `MemorySaver`，不属于持久化。
- RagAgentService 与 AIOpsService 已使用共享 Checkpointer Factory。
- VectorStoreManager 改为显式首次使用时初始化，测试收集不再连接 Milvus。
- 配置、模型和 API 错误使用 Pydantic v2 写法，并清洗对外错误中的常见凭据。
- Makefile 的测试、Lint 和类型检查统一使用项目 `.venv`，并检查被根级
  `models/` 忽略规则意外覆盖的 `app/models`。

## 2. 验收结果

以下命令均于 2026-08-23 在 Python 3.13.9 环境通过：

```text
make test-quick  -> 20 passed
make lint        -> All checks passed
make type-check  -> Success: no issues found in 43 source files
make check-all   -> format、lint、20 tests 全部通过
```

测试覆盖率输出为当前基线观测值，不作为质量成绩或简历量化指标。

## 3. 兼容性与限制

- 旧 API 路径和已有 SSE 事件类型未修改。
- 当前仍是通用工具调用 Agent 加 AIOps Plan-Execute-Replan 工作流，不是完整 Multi-Agent。
- `MemorySaver` 仍是进程内开发状态，服务重启后不会恢复。
- Specialist、Supervisor、Reviewer、统一 Agent API 与统一 SSE 尚未实现。
- MCP 工具仅在首次使用或显式预热时发现；单个 Server 不可用会记录为
  `unavailable`，其余 Server 和本地工具继续可用。

## 4. 下一阶段

按主计划进入 M2，先实施阶段 2 Knowledge Agent。开始前应将其验收标准固化为
固定文档夹具、Evidence/引用契约、空结果与检索失败的差异测试，并确认 Knowledge
Agent 只能从 Registry 获得知识工具。
