# PostgreSQL 共享持久化实施交接

> 日期：2026-08-28  
> 分支：`multi-agents`  
> 状态：计划范围已实现并验收  
> 对应计划：`xzh_docs/plan_docs/postgresql-persistence-migration-plan.md`

## 1. 实施结果

项目已在保留 memory 和 SQLite 后端的前提下，增加 PostgreSQL 共享持久化。PostgreSQL
模式同时覆盖：

- Multi-Agent 顶层 LangGraph Checkpoint。
- 公开 run、thread、event、trace 和 metrics 存储。
- 多个应用实例共享读取和删除。
- SQLite 公开运行历史的一次性、只读、幂等迁移。

默认后端仍为 SQLite，不配置 PostgreSQL 时现有本地开发流程不变。选择 PostgreSQL 后，连接
失败或 Schema 缺失会阻止应用启动，不会静默退回 SQLite。

## 2. 主要实现

### 配置与依赖

- `app/config.py` 将持久化后端限制为 `memory | sqlite | postgres`。
- 新增数据库 URL、连接池上下限和连接超时配置。
- 增加 `langgraph-checkpoint-postgres`、`psycopg`、`psycopg-pool` 和 Alembic。
- `pyproject.toml` 与 `uv.lock` 已同步。

### 统一持久化生命周期

- `app/services/persistence.py` 成对创建 Checkpointer 与 RunStore。
- PostgreSQL 使用 FastAPI lifespan 管理的有界异步连接池。
- `MultiAgentService` 不再根据具体数据库类型创建 RunStore。
- 数据库资源在启动时打开，在应用关闭时释放。

### PostgreSQL RunStore

- `app/services/run_store.py` 新增 `PostgresRunStore`。
- run 与 events 在一个事务内写入。
- 重复 `run_id` 使用 upsert，保留首次创建时间并完整替换事件集合。
- JSONB 读取后仍通过 Pydantic 公共模型校验。
- 外键级联删除 events。
- `MemoryRunStore` 同步修正为幂等保存，保持三个后端的契约一致。

### Schema 管理

- Alembic 管理项目拥有的 `agent_runs` 与 `agent_events`。
- LangGraph 官方 `AsyncPostgresSaver.setup()` 管理 Checkpoint 表。
- `python -m app.cli.database upgrade` 按顺序初始化两套 Schema。
- 应用正常启动只验证 Schema，不并发执行 DDL。

### 数据迁移

- `app/cli/migrate_sqlite.py` 只读 SQLite 源文件。
- 每个 response、event 和 metrics 在写入 PostgreSQL 前经过 Pydantic 校验。
- 重复执行使用 PostgreSQL upsert，不删除源文件。
- 不复制 LangGraph SQLite Checkpoint 内部表。

### 部署与文档

- 新增 `postgres-database.yml`，包含健康检查和独立数据目录。
- Makefile 新增 PostgreSQL 启停、状态、Schema 升级和 SQLite 历史迁移命令。
- `.env.example` 增加无真实凭据的配置占位符。
- README 已说明默认 SQLite、可选 PostgreSQL、初始化、迁移和能力边界。

## 3. 验证结果

使用隔离的 PostgreSQL 17 容器从空库执行了业务 Schema 和 LangGraph Checkpoint Schema
初始化，并运行完整测试集。

通过的 PostgreSQL 专项行为包括：

- 两个独立 `MultiAgentService` 实例共享 run 和顶层 Checkpoint。
- 关闭/重开资源后读取持久化状态。
- 12 个 run 并发写入且每条 Trace 事件完整。
- 重复 run upsert 不产生重复历史，旧 events 被完整替换。
- event 写入冲突时 run 与 events 整体回滚。
- 删除 thread 同时清理公开记录和顶层 Checkpoint。
- 缺失 Checkpoint Schema 时明确拒绝启动。
- SQLite 公开历史真实迁移到 PostgreSQL 后可恢复 run 和 events。
- 持久化失败时 SSE 仍只产生一个 `run_failed` 终态。

最终执行结果：

```text
make check-all
  Ruff: passed
  Tests: 110 passed
  Coverage: 69.42%

make type-check
  Mypy: success, 85 source files

ruff check migrations
  passed
```

全量测试在设置 `TEST_POSTGRES_URL` 的情况下运行，因此 6 项 PostgreSQL 测试没有跳过。

## 4. 使用方式

在本地 `.env` 中配置 PostgreSQL 占位项对应的实际值，并执行：

```bash
make postgres-up
make db-upgrade
```

然后设置：

```env
CHECKPOINTER_BACKEND=postgres
DATABASE_URL=postgresql://<user>:<password>@localhost:5432/oncall_agent
```

如需迁移现有公开运行历史，先暂停新请求、等待 run 结束并备份 SQLite，然后执行：

```bash
make db-migrate-sqlite SQLITE_PATH=data/multi_agent.sqlite3
```

测试 PostgreSQL 后端：

```bash
TEST_POSTGRES_URL=postgresql://<user>:<password>@localhost:5432/oncall_agent \
  pytest -m postgres tests/integration/test_postgres_persistence.py
```

## 5. 保留边界

本次没有改造：

- Task Agent 子图自己的 `MemorySaver`。
- 旧 RAG 对话链路和旧 AIOps 兼容链路的进程内状态。
- Redis、分布式限流、缓存、锁或队列。
- 多租户、RBAC、数据库高可用和自动故障切换。
- SQLite 与 PostgreSQL 的实时双写。
- LangGraph Checkpoint 跨后端历史转换。

因此当前可以表述为“Multi-Agent 顶层 Checkpoint 与公开 Trace 支持 PostgreSQL 多实例共享”，
不能表述为“所有子图和兼容接口均已持久化”或“已实现生产级高可用”。

## 6. 回滚

SQLite 后端与原文件均被保留。切回 SQLite 前应停止新请求，避免 PostgreSQL 切换期间的新
数据与旧 SQLite 分叉。修改配置为：

```env
CHECKPOINTER_BACKEND=sqlite
CHECKPOINT_PATH=data/multi_agent.sqlite3
```

本次没有实现双写，因此 PostgreSQL 中新增的数据不会自动回写 SQLite。
