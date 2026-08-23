# M5 工程化与项目证据实施交接

> 完成时间：2026-08-23 CST  
> 分支：`multi-agents`  
> 范围：阶段 8～9（持久化、可观测性、Eval、业务资料与项目文档）

## 1. 完成内容

- 新增 `langgraph-checkpoint-sqlite`，默认通过 FastAPI lifespan 打开
  `AsyncSqliteSaver`，关闭时释放连接；导入阶段和普通单元测试仍使用隔离的 MemorySaver。
- 默认数据库为被 Git 忽略的 `data/multi_agent.sqlite3`。LangGraph Checkpoint 与公共运行索引
  使用同一文件、不同表；SQLite 的定位是本地单实例重启恢复，不宣称生产级多实例存储。
- 新增 SQLite/内存 `RunStore`，持久化公开请求消息、标准化响应、安全事件和单次运行指标；
  `thread_id` 可在服务实例重建后恢复，删除 thread 时同步删除运行索引和对应图 Checkpoint。
- 新增 `GET /api/agent/runs/{run_id}/trace` 和 `GET /api/agent/metrics`。Trace 包含 Agent、
  node、tool、状态、耗时等安全事件；聚合指标包含失败率、超时率、平均耗时、调度数、工具
  调用数和任务步骤数。
- 当前调用链没有可靠的 provider usage 与价格信息，因此模型调用数、Token 数和 Token 成本
  明确返回 `null`，没有伪造为 0；后续接入统一 usage callback 后才能提供真实成本统计。
- 修正顶层流式图在异步 SQLite Saver 下的状态读取，使用 `aget_state`，并用持久化流式集成
  测试验证只产生一个终态事件。
- 建立 34 条固定离线 Eval，覆盖路由、检索引用契约、工具计划白名单/调用上限、Task 终态
  聚合和 Reviewer 确定性检查。报告记录数据集 SHA-256、全部逐案例结果和实现标签。
- 提交 M4 契约基线与 M5 验收快照；同一数据集均为 34/34，差值 0。M5 没有把这一契约
  通过率描述为线上准确率、召回率、Groundedness 或生产性能。
- 新增 `make eval`，固定基线对比报告生成到 `artifacts/evals/latest.json`。
- 新增 `business-docs/`：产品手册、售后规则、服务等级、交付指南和 FAQ；默认文档上传目录
  从 `aiops-docs/` 切换为该目录，旧运维资料保留。
- 新增只读 `BusinessTicket` MCP Server（8005），提供 `get_ticket` 和 `search_tickets`，使用
  有限、脱敏、合成数据；加入配置、Makefile、Windows 启停脚本和 MCP 文档。
- 项目包名改为 `enterprise-knowledge-multi-agent`，版本改为 2.0.0；同步 FastAPI 名称、
  README、`.env.example`、真实架构图、执行时序、演示用例和简历证据映射。

## 2. 验收证据

自动化验收不读取 `.env`，不依赖真实 DashScope、Milvus 或在线 MCP：

```text
ONCALL_DISABLE_ENV_FILE=1 make test-quick
  -> 98 tests passed（包含前端可读性与全局引用编号回归）

ONCALL_DISABLE_ENV_FILE=1 make lint
  -> Ruff passed

ONCALL_DISABLE_ENV_FILE=1 make type-check
  -> Success: no issues found in 80 source files

make eval
  -> 34/34，baseline_score=1.0，current_score=1.0，same_dataset=true

git diff --check
  -> passed
```

持久化验收覆盖：

- 关闭并重新打开 `AsyncSqliteSaver` 后，指定 LangGraph thread 的状态仍可读取。
- 重建 `MultiAgentService` 后，指定 `thread_id` 的运行 ID 和公开消息仍可读取。
- 重启 store 后，可按 `run_id` 恢复响应、完整安全事件序列和单次指标。
- 异步 SQLite Checkpointer 下，统一 SSE 仍只产生一个正常终态并写入 Trace。

业务 MCP 另做了本地协议级验收：启动 `ticket_server.py` 后，LangChain MCP Adapter 成功发现
`get_ticket`、`search_tickets` 并实际调用 `get_ticket(TKT-1001)`；验收后已关闭进程并移除
本次生成的 PID/日志文件。

`bandit` 未运行：当前虚拟环境未安装该可选工具（`No module named bandit`）。Ruff、mypy、
全量测试、固定 Eval 和 MCP 协议级检查均已执行。

## 3. 关键入口

- 持久化生命周期：`app/agent/runtime/checkpointer.py`、`app/main.py`
- 运行索引和聚合指标：`app/services/run_store.py`
- 统一服务与 Trace 生成：`app/services/multi_agent_service.py`
- Trace/metrics API：`app/api/agent.py`、`app/models/agent.py`
- 固定 Eval：`app/evaluation/runner.py`、`tests/evals/fixtures/m5_eval_dataset.json`
- Eval 说明与快照：`xzh_docs/eval_reports/`
- 业务数据：`business-docs/`、`mcp_servers/ticket_server.py`
- 架构与演示：`xzh_docs/architecture/`、`xzh_docs/demo/m5-demo-scenarios.md`

## 4. 明确保留的限制

- SQLite 只适合本地演示和单实例恢复；多 Worker/多实例部署需改为 PostgreSQL 等共享后端，
  并补连接池、迁移、保留期和容量治理。
- 当前只持久化顶层 LangGraph Checkpoint；Task Agent 内部节点使用内存 Checkpointer，但其最终
  `AgentResult` 会进入顶层持久状态。若要从 Task 中间步骤断点续跑，需要单独设计子图恢复契约。
- 未采集真实模型 usage、Token 和价格，不提供成本数字。
- 34 条 Eval 是确定性离线契约集，不覆盖真实模型语义质量、真实 Milvus 检索质量或线上 MCP
  数据质量；这些指标必须在固定在线/集成数据集和可控环境具备后另行建立。
- 旧 `/api/chat`、`/api/chat_stream`、`/api/aiops` 和旧源码仍保留，未获得删除授权。
- 本轮没有进行并发压测，因此不声明 QPS、并发量或生产可用性。

## 5. 后续建议

M1～M5 的当前计划范围已经完成。下一轮如继续工程化，建议优先做 PostgreSQL Checkpointer/
RunStore、统一 LLM usage callback、真实检索 Eval 数据集和数据库保留期策略；这些属于 M5
之后的增强，不应回写成当前已经具备的能力。
