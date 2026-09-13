# PostgreSQL 工作记忆阶段一实施交接

> 日期：2026-09-13
>
> 分支：`multi-agents`
>
> 状态：阶段一已完成并通过验收

## 1. 本阶段结论

已完成 PostgreSQL 跨轮工作记忆的基础层，但尚未把记忆加载进 Supervisor、Specialist 或
Reviewer，也尚未实现每轮 MemoryUpdate 和 LLM 压缩。因此当前准确表述是：

> 已具备 PostgreSQL 结构化工作记忆契约、Schema、CAS Store 和动态 Evidence 时间语义。

不得在阶段二、三完成前描述为“跨轮回答已经自动连续”。

## 2. 已完成内容

- 新增 `app/agent/memory/contracts.py`：
  - `WorkingMemoryContext`
  - `MemoryItem`
  - `MemoryEvidenceRef`
  - `MemoryItemProposal`
  - `WorkingMemoryUpdate`
- 为结构化记忆增加长度、数量、时区、Evidence 引用、动态观察时间和时间顺序校验。
- 扩展内部 `Evidence` 契约，增加：
  - `observed_at`
  - `temporal_scope`
  - `valid_until`
  - `query_scope`
- Data Agent 在只读工具成功后使用服务端时钟记录观察时间，并根据集中配置计算 TTL。
- 工具参数不直接进入 `query_scope`；只保存稳定 SHA-256 指纹，避免持久化敏感参数。
- 新增集中时间策略 `app/agent/memory/policy.py`，区分告警、监控指标、工单和默认动态数据。
- 新增 Alembic revision `20260913_0002`，创建 `agent_working_memories` 及必要约束。
- 新增 `PostgresWorkingMemoryStore`：
  - Schema 检查
  - `load`
  - `create`
  - `compare_and_swap`
  - `delete_thread`
- Store 写入前重新执行完整 Pydantic 校验，避免 `model_copy(update=...)` 绕过不变量。
- CAS 同时校验 `thread_id`、`owner_user_id` 和预期版本，线程所有者不会被更新操作静默替换。
- PostgreSQL 模式下由 `PersistenceFactory` 复用现有 lifespan 连接池创建 Store。
- memory 和 SQLite 模式明确返回 `working_memory_store=None`，不创建伪工作记忆后端。
- `MultiAgentService.startup()` 接收 Store 并记录是否启用，但本阶段不读写业务记忆。
- 新增 Fake Store，普通测试不依赖真实 PostgreSQL、LLM、Milvus 或 MCP。

## 3. PostgreSQL Schema

新增表：

```text
agent_working_memories
├── thread_id             TEXT PRIMARY KEY
├── owner_user_id         TEXT NOT NULL
├── schema_version        INTEGER NOT NULL DEFAULT 1
├── version               BIGINT NOT NULL DEFAULT 1
├── context_json          JSONB NOT NULL
├── last_run_id           TEXT NULL
├── created_at            TIMESTAMPTZ NOT NULL
└── updated_at            TIMESTAMPTZ NOT NULL
```

约束包括：

- `schema_version >= 1`
- `version >= 1`
- `context_json` 必须是 JSON object
- `updated_at >= created_at`

没有增加 JSONB GIN 索引，因为当前访问路径是按 `thread_id` 读取整个快照。

## 4. 配置

`app/config.py` 已增加工作记忆开关、上下文预算、条目上限、压缩阈值、冲突重试、压缩超时和
动态数据 TTL。阶段一实际使用的是：

- `working_memory_enabled`
- 告警、监控指标、工单和默认动态数据 TTL

其他预算配置为后续阶段预留，并已增加范围及相互关系校验。正式 PostgreSQL 环境升级前必须
执行：

```bash
make db-upgrade
```

应用不会在启动时隐式创建生产表；启用 PostgreSQL 工作记忆但 Schema 缺失时会明确启动失败。

## 5. 验收结果

执行结果：

```text
阶段一相关单元测试：25 passed
完整测试（临时 PostgreSQL 17，未跳过 PostgreSQL 用例）：127 passed
PostgreSQL 集成测试：8 passed
Ruff：通过
Mypy：通过，89 个源码文件无错误
git diff --check：通过
```

真实 PostgreSQL 测试使用无持久卷的临时 `postgres:17-alpine` 容器，测试完成后容器已停止并
自动删除。没有读取本地 `.env`，也没有使用开发或生产数据库。

PostgreSQL 验收覆盖：

- Alembic upgrade。
- 工作记忆表及约束存在。
- create/load/delete。
- 跨连接池重新打开后恢复。
- CAS 更新。
- 两个并发相同 expected version 只有一个成功。
- 现有 RunStore、Checkpoint、事务回滚和 SQLite 迁移回归。

## 6. 阶段一验收清单

- [x] PostgreSQL 可以保存并跨连接加载结构化工作记忆。
- [x] 并发写入不会发生无提示覆盖。
- [x] 动态 Data Evidence 由服务端写入带时区的观察时间和有效期。
- [x] 工具查询范围只保存不可逆指纹，不保存原始参数。
- [x] 用户声明和 supported Evidence 条目具有明确契约边界。
- [x] PostgreSQL Schema 由 Alembic 管理，启动只校验、不隐式建表。
- [x] memory/SQLite 模式不创建正式工作记忆后端。
- [x] 普通测试使用 Fake，不依赖外部服务。
- [x] 公共 JSON/SSE 契约没有变化。
- [x] 没有宣称尚未实现的跨轮加载、自动更新或压缩能力。

## 7. 下一阶段入口

阶段二从以下工作开始：

1. 为 RunStore 增加有界 `get_recent_thread()`。
2. 实现 Freshness Policy、Context Builder 和角色化 Renderer。
3. 在 JSON/SSE 调图前加载 PostgreSQL 工作记忆和最近对话。
4. 扩展 `MultiAgentState`、Supervisor 输入和 `AgentTask.context` 注入。
5. 让 Reviewer 检查过期观察和未验证声明。
6. 将 Task 子图执行键补充 `run_id`，防止跨 run 状态碰撞。

阶段二开始前不需要重做阶段一 Store，但应保留 CAS、时区和 Schema 不变量。
