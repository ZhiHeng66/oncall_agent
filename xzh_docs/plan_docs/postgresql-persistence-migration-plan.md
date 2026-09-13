# PostgreSQL 共享持久化改造计划

> 项目：企业知识服务多智能体协同平台  
> 文档日期：2026-08-28  
> 文档状态：已实施并完成计划范围验收  
> 适用分支：`multi-agents`  
> 目标范围：Multi-Agent 顶层 LangGraph Checkpoint 与公开 RunStore  
> 非目标：Redis、全量历史 Checkpoint 转换、旧兼容链路持久化改造

> 实施结果（2026-08-28）：P0～P5 已按本文第一阶段范围完成。项目保留 memory 与 SQLite，
> 新增 PostgreSQL 共享持久化、业务 Schema 迁移、LangGraph Checkpoint 初始化、SQLite 公开
> 历史迁移、本地 Compose 和真实 PostgreSQL 集成测试。实际变更与验证结果见
> `xzh_docs/handoff_docs/2026-08-28-postgresql-persistence-implementation-handoff.md`。

## 1. 改造结论

本项目不应直接删除 SQLite 或把所有环境强制切换到 PostgreSQL。推荐在现有
`memory`、`sqlite` 能力上增加 `postgres` 后端，形成按部署场景选择的三种模式：

| 后端 | 主要场景 | LangGraph Checkpointer | 公开 RunStore |
|---|---|---|---|
| `memory` | 单元测试、显式无持久化运行 | `MemorySaver` | `MemoryRunStore` |
| `sqlite` | 本地开发、单实例演示 | `AsyncSqliteSaver` | `SqliteRunStore` |
| `postgres` | 多 Worker、多实例、共享持久化 | `AsyncPostgresSaver` | `PostgresRunStore` |

第一阶段必须同时完成顶层 Checkpointer 和 RunStore 的 PostgreSQL 化，避免出现运行记录已
进入 PostgreSQL、工作流状态仍保存在本地 SQLite 的“半迁移”状态。

本次改造不改变 Agent 数量、Agent 权限、图路由、公共 API、Pydantic 响应或 SSE 事件协议。
PostgreSQL 是持久化基础设施，不是 Agent，也不应改变 Specialist 的职责边界。

---

## 2. 当前实现基线

### 2.1 SQLite 当前承担的职责

Multi-Agent 主链路目前使用同一个 SQLite 文件承担两类逻辑上独立的职责。

第一类是 LangGraph Checkpoint：

- 创建入口：`app/agent/runtime/checkpointer.py`。
- 当前默认实现：`AsyncSqliteSaver`。
- 状态键：顶层图使用 `thread_id:run_id` 作为 LangGraph `thread_id`。
- 主要用途：保存顶层图状态快照、节点写入和运行恢复所需状态。
- 生命周期：由 `MultiAgentService.startup()` 在 FastAPI lifespan 内打开，由
  `shutdown()` 关闭。

第二类是公开运行记录与安全 Trace：

- 实现入口：`app/services/run_store.py`。
- 当前实现：`SqliteRunStore`。
- `agent_runs` 保存请求消息、标准化响应、指标和创建时间。
- `agent_events` 保存标准化 SSE 事件及事件顺序。
- API 通过 RunStore 查询 run、thread、trace 和聚合 metrics。
- RunStore 不直接向 API 暴露原始模型响应、原始 MCP 返回值或异常栈。

当前关系如下：

```text
FastAPI lifespan
    |
    v
MultiAgentService.startup()
    |-- CheckpointerFactory.open()
    |      `-- AsyncSqliteSaver
    |
    |-- MultiAgentGraph.set_checkpointer()
    |
    `-- SqliteRunStore.setup()
           |-- agent_runs
           `-- agent_events
```

### 2.2 当前配置耦合

`app/config.py` 当前包含：

```text
CHECKPOINTER_BACKEND=sqlite
CHECKPOINT_PATH=data/multi_agent.sqlite3
```

`MultiAgentService.startup()` 根据 `checkpointer_backend == "sqlite"` 同时选择
`SqliteRunStore`。这意味着：

- Checkpointer 后端配置同时承担了 RunStore 选择职责。
- SQLite 文件路径同时被两类存储使用。
- Service 层知道具体存储类型。
- 新增 PostgreSQL 时如果继续添加条件分支，生命周期和配置会进一步耦合。

本次改造应把后端选择和资源生命周期收敛到持久化工厂中，不在 Service 层继续堆叠数据库
判断。

### 2.3 当前持久化边界

本计划只针对 Multi-Agent 顶层主链路。以下状态当前仍是进程内状态：

- `app/agent/specialists/task/graph.py` 中 Task Agent 子图使用自己的 `MemorySaver`。
- `app/services/rag_agent_service.py` 中旧 RAG 对话链路使用 `MemorySaver`。
- 旧 AIOps 兼容工作流的状态不属于本次迁移目标。

因此第一阶段完成后，只能描述为：

> Multi-Agent 顶层工作流 Checkpoint、运行记录与安全 Trace 支持 PostgreSQL 共享持久化。

不得描述为“项目所有子图和所有兼容接口都已经持久化”，也不得把 Checkpoint 描述为长期
用户记忆。

---

## 3. 改造目标与非目标

### 3.1 目标

1. 增加 PostgreSQL 作为可选持久化后端，同时保留 memory 和 SQLite。
2. 使用 LangGraph 官方 PostgreSQL Checkpointer 保存顶层图状态。
3. 使用异步 PostgreSQL RunStore 保存 run、event、trace 和 metrics。
4. 使用连接池支持多个 FastAPI Worker 或多个应用实例访问共享数据库。
5. 保持 `/api/agent/run`、`/api/agent/run_stream` 及查询、删除接口契约不变。
6. 保持每条 SSE 流最多一个终态事件。
7. 对数据库连接、事务、删除和恢复失败保持可观察，不伪造成功。
8. 提供可复现的本地 PostgreSQL 环境、Schema 迁移命令和集成测试。
9. 提供从现有 SQLite RunStore 迁移历史运行数据的可选方案。
10. 提供明确的上线、验证和回滚流程。

### 3.2 非目标

第一阶段不实施：

- Redis 缓存、限流、锁、队列或 Pub/Sub。
- 多租户、RBAC、统一身份认证和文档级权限。
- 将 `user_id` 新增为数据库查询维度。
- 把 Task Agent 子图改成 PostgreSQL Checkpointer。
- 改造旧 `/api/chat`、`/api/chat_stream` 或 `/api/aiops` 的状态后端。
- 直接复制 SQLite Checkpoint 内部表到 PostgreSQL。
- 修改 Agent 路由、Reviewer 行为、工具权限或 RAG 策略。
- 修改公共 API 和 SSE 字段。
- 删除 SQLite 后端、SQLite 文件或旧兼容代码。
- 声称高可用、生产可用或具体并发指标。

---

## 4. 目标架构

### 4.1 资源生命周期

建议引入统一、生命周期感知的持久化资源工厂。工厂根据有限枚举选择成对的 Checkpointer
与 RunStore：

```text
FastAPI lifespan
    |
    v
PersistenceFactory.open(backend)
    |
    |-- memory
    |     |-- MemorySaver
    |     `-- MemoryRunStore
    |
    |-- sqlite
    |     |-- AsyncSqliteSaver
    |     `-- SqliteRunStore
    |
    `-- postgres
          |-- AsyncConnectionPool
          |-- AsyncPostgresSaver
          `-- PostgresRunStore
```

`MultiAgentService` 只负责消费已经创建的资源：

1. 在启动阶段取得 Checkpointer 和 RunStore。
2. 将 Checkpointer 注入顶层 `MultiAgentGraph` 并重新编译图。
3. 使用 RunStore 保存和查询公开运行数据。
4. 在关闭阶段释放持久化资源。

Service 不应知道 PostgreSQL 连接池参数、DDL、SQLite 文件创建或 LangGraph Checkpoint 表
细节。

### 4.2 连接池

PostgreSQL 后端使用一个 FastAPI lifespan 管理的异步连接池。建议优先使用 `psycopg` 与
`psycopg_pool.AsyncConnectionPool`，并评估将同一个池提供给 `AsyncPostgresSaver` 和
`PostgresRunStore`。

连接池要求：

- 不在模块 import 阶段连接数据库。
- 不为每个请求创建新池。
- 最小连接数、最大连接数、连接超时从 `app/config.py` 读取。
- 应用关闭时等待资源正常释放。
- 日志不得输出包含密码的完整 DSN。
- PostgreSQL 被选中但连接失败时，应用启动明确失败，不静默降级到 SQLite。

静默降级会使不同实例写入不同数据源，破坏共享状态不变量，因此不属于可接受的故障恢复。

### 4.3 Schema 所有权

数据库表按所有权分成两组：

- LangGraph Checkpoint 表由 `langgraph-checkpoint-postgres` 管理。
- `agent_runs`、`agent_events` 等项目业务表由项目迁移工具管理。

不得把 LangGraph 的内部建表 SQL 复制到项目迁移文件。否则 LangGraph 升级时容易出现内部
Schema 与依赖版本不一致。

生产或共享环境中，数据库初始化应是显式部署步骤，而不是让多个应用实例在启动时并发执行
DDL。应用启动阶段应验证连接和所需 Schema 是否存在。

---

## 5. 配置设计

### 5.1 配置项

建议保留当前配置名称以降低兼容迁移成本，并扩展其取值：

```env
CHECKPOINTER_BACKEND=postgres
DATABASE_URL=postgresql://username:password@localhost:5432/oncall_agent

POSTGRES_POOL_MIN_SIZE=1
POSTGRES_POOL_MAX_SIZE=10
POSTGRES_CONNECT_TIMEOUT_SECONDS=10
```

现有 SQLite 配置继续有效：

```env
CHECKPOINTER_BACKEND=sqlite
CHECKPOINT_PATH=data/multi_agent.sqlite3
```

配置规则：

- `checkpointer_backend` 使用 `Literal["memory", "sqlite", "postgres"]` 或等价枚举。
- `sqlite` 必须提供有效的本地 `checkpoint_path`。
- `postgres` 必须提供非空 `database_url`。
- `memory` 不应隐式创建 SQLite 文件或 PostgreSQL 连接。
- 池最小值不得小于零，最大值必须大于零且不小于最小值。
- 配置错误在启动前暴露，不延迟到第一个用户请求。

### 5.2 后端一致性

第一阶段不增加可由用户任意组合的 `RUN_STORE_BACKEND`。一个后端选择应返回匹配的
Checkpointer 与 RunStore，避免以下无效生产配置：

```text
Checkpointer = PostgreSQL
RunStore     = SQLite
```

如果测试需要独立替换 RunStore，继续使用现有构造参数依赖注入，而不是扩大生产配置面。

### 5.3 安全要求

- `.env.example` 只提供占位符，不包含真实凭据。
- 不读取、打印或迁移开发者现有 `.env`。
- 日志只记录后端名称、数据库主机的安全摘要或连接状态，不记录完整 DSN。
- 对外错误不得包含账号、密码、主机内部信息、SQL 文本或异常栈。
- 服务端日志可保留经过清洗的异常类别和操作上下文。

---

## 6. 依赖与迁移工具

计划评估并增加：

| 依赖 | 用途 |
|---|---|
| `langgraph-checkpoint-postgres` | LangGraph PostgreSQL Checkpointer |
| `psycopg` | PostgreSQL 异步驱动 |
| `psycopg-pool` | 生命周期感知的异步连接池 |
| `alembic` | 项目业务表版本化迁移 |

实施前应根据当前 `uv.lock` 和已安装 LangGraph 主版本选择相容版本，并更新锁文件。不得仅修改
`pyproject.toml` 而遗漏锁文件。

Alembic 只管理项目拥有的表。首次引入时至少需要：

- Alembic 配置。
- 初始 `agent_runs`、`agent_events` 迁移。
- 本地 upgrade 命令。
- 测试数据库 upgrade fixture。
- 回滚策略说明。

---

## 7. RunStore 数据模型

### 7.1 `agent_runs`

建议第一阶段使用以下语义：

| 字段 | PostgreSQL 类型 | 约束/用途 |
|---|---|---|
| `run_id` | `TEXT` | 主键 |
| `thread_id` | `TEXT` | 非空，会话查询 |
| `request_message` | `TEXT` | 非空，公开请求消息 |
| `response_json` | `JSONB` | 非空，标准化 `AgentRunResponse` |
| `metrics_json` | `JSONB` | 非空，标准化 `AgentRunMetrics` |
| `created_at` | `TIMESTAMPTZ` | 非空，默认当前时间 |

索引：

```text
(thread_id, created_at, run_id)
```

`run_id` 暂时保留文本类型。当前测试和内部依赖注入允许 `run-1` 等非 UUID 值，强制改成
PostgreSQL UUID 会引入不必要的契约变化。

### 7.2 `agent_events`

| 字段 | PostgreSQL 类型 | 约束/用途 |
|---|---|---|
| `event_id` | `TEXT` | 主键 |
| `run_id` | `TEXT` | 非空，关联 `agent_runs` |
| `sequence_no` | `INTEGER` | 非空，事件顺序 |
| `envelope_json` | `JSONB` | 非空，标准化事件信封 |

约束与索引：

- 外键：`run_id -> agent_runs(run_id) ON DELETE CASCADE`。
- 唯一约束：`(run_id, sequence_no)`。
- 查询索引：`(run_id, sequence_no)`。

### 7.3 写入事务

一次 `RunStore.save()` 必须在同一事务中完成：

1. Upsert `agent_runs`。
2. 删除该 run 旧事件。
3. 按顺序批量写入新事件。
4. 提交事务。

任何一步失败都应回滚全部写入。不能产生 run 已存在但事件只有一部分的公开 Trace。

SQLite 的 `INSERT OR REPLACE` 与 PostgreSQL 语义不同。PostgreSQL 应显式使用
`INSERT ... ON CONFLICT (run_id) DO UPDATE`，并在测试中固定以下行为：

- 重复保存同一 `run_id` 不产生重复 run。
- events 被完整替换而不是追加重复项。
- `created_at` 是保留首次创建时间还是随覆盖更新，必须由测试明确；推荐保留首次创建时间。

### 7.4 查询行为

第一阶段保持当前 RunStore 契约：

- `get_run()` 不存在时返回 `None`。
- `get_trace()` 不存在时返回 `None`。
- `get_thread()` 不存在时返回空 run 列表和空 history。
- thread 历史按 `created_at, run_id` 稳定排序。
- 从 JSONB 读取后仍通过 Pydantic 模型校验。
- `metrics()` 的计算结果与当前 SQLite 实现一致。

为降低迁移风险，第一阶段继续在 Python 中聚合 metrics。只有出现真实数据规模和查询性能需求
后，再设计数据库聚合或预聚合表。

---

## 8. Checkpoint 设计

### 8.1 顶层状态键保持不变

顶层图继续使用：

```text
LangGraph thread_id = public thread_id + ":" + run_id
```

这样每次 run 的内部图状态互相隔离，不会因为共享公开 `thread_id` 而把上一轮完整状态意外合并
进新 run。

### 8.2 删除语义

当前 `clear_thread()` 先删除 RunStore 中的 thread 数据，再逐个调用
`delete_run_state(thread_id, run_id)` 删除 Checkpoint。RunStore 和 LangGraph Checkpointer 即使位于
同一个 PostgreSQL 数据库，也由不同组件管理，不能假设存在一个稳定的跨组件公共事务。

实施阶段必须明确并测试失败语义：

- 正常路径删除公开 run、events 和对应 Checkpoint。
- Checkpoint 删除失败不能返回伪造成功。
- 重试删除必须幂等。
- 不向客户端暴露数据库异常详情。
- 必要时允许留下不可公开查询的孤立 Checkpoint，并通过日志和后续清理任务处理；不得静默忽略。

第一阶段不为这一问题引入复杂的分布式事务。应选择简单、可测试、失败可见的顺序，并在交接
文档中记录残留状态边界。

### 8.3 Checkpoint Schema 初始化

`AsyncPostgresSaver.setup()` 或对应的官方初始化能力只应在明确的数据库初始化命令中执行。
应用正常启动负责：

1. 打开连接池。
2. 验证数据库可达。
3. 验证所需表已存在。
4. 创建 Saver 并编译顶层图。

这可避免多个 Worker 同时执行 DDL，也让部署失败发生在流量进入之前。

---

## 9. 分阶段实施计划

### 阶段 P0：基线与设计冻结

目标：在改动存储实现前固定当前行为。

任务：

- 补齐 `RunStore` 后端无关契约测试。
- 固定重复保存、排序、删除、空查询和 metrics 语义。
- 固定顶层 Checkpoint 的重启恢复和 run 隔离语义。
- 确认当前 API 和 SSE 契约测试全部通过。
- 记录当前 SQLite 数据表及运行恢复边界。

退出条件：

- 不依赖 PostgreSQL 的基线测试稳定通过。
- 关键行为不再依赖阅读具体 SQLite SQL 才能判断。

### 阶段 P1：配置与依赖

目标：建立 PostgreSQL 配置和依赖基础，不改变默认运行后端。

任务：

- 增加 PostgreSQL 依赖并更新 `uv.lock`。
- 将后端配置改为有限类型。
- 增加数据库 URL、连接池和超时配置。
- 更新 `.env.example` 占位符。
- 为配置合法性增加单元测试。
- 默认值继续使用 SQLite。

退出条件：

- 未配置 PostgreSQL 时本地开发行为不变。
- PostgreSQL 配置错误能在启动阶段被发现。

### 阶段 P2：PostgreSQL Schema 与 RunStore

目标：完成项目业务表和 PostgreSQL RunStore。

任务：

- 初始化 Alembic。
- 创建 `agent_runs` 与 `agent_events` 初始迁移。
- 实现 `PostgresRunStore`。
- 使用参数化 SQL，不拼接用户输入。
- 使用单事务保存 run 与 events。
- 保持 Pydantic 校验和错误清洗。
- 对 RunStore Protocol 运行统一契约测试。

退出条件：

- SQLite 和 PostgreSQL RunStore 契约测试结果一致。
- PostgreSQL 关闭并重开后可恢复 run、thread、trace 和 metrics。

### 阶段 P3：PostgreSQL Checkpointer 与统一生命周期

目标：把顶层 LangGraph Checkpoint 与 RunStore 作为成对资源接入应用。

任务：

- 增加 `AsyncPostgresSaver`。
- 引入 lifespan 管理的 PostgreSQL 异步连接池。
- 设计 `PersistenceResources` 和持久化工厂。
- 从 `MultiAgentService.startup()` 移除具体数据库条件判断。
- 将 PostgreSQL Saver 注入顶层图并重新编译。
- 保持 shutdown 幂等和资源正确释放。
- 增加 Schema 初始化与升级命令。

退出条件：

- PostgreSQL 模式下 Checkpointer 与 RunStore 都不再使用本地 SQLite。
- 两个 Service 实例可读取同一数据库中的运行记录和 Checkpoint。

### 阶段 P4：本地部署与集成测试

目标：提供可复现、与开发者真实环境隔离的 PostgreSQL 验证方式。

任务：

- 新增独立 PostgreSQL Compose 配置。
- 增加容器健康检查和持久化数据卷。
- 增加启动、停止、状态、初始化和迁移命令。
- 增加显式 PostgreSQL pytest marker。
- 在测试数据库上执行 Alembic upgrade。
- 增加多实例、并发写入、重启恢复和连接失败测试。

退出条件：

- 新开发环境能根据文档从空数据库复现测试。
- 普通测试不依赖 PostgreSQL，集成测试明确依赖本地或 CI 数据库。

### 阶段 P5：数据迁移、文档与切换演练

目标：验证从 SQLite 到 PostgreSQL 的可控切换和回滚。

任务：

- 提供可选的一次性 RunStore 数据迁移命令。
- 校验 run 数、event 数、事件顺序和抽样模型反序列化。
- 演练停止写入、迁移、切换、冒烟和回滚。
- 更新 README、架构文档和部署说明。
- 创建实施交接文档并记录实际检查结果。

退出条件：

- 迁移失败不会删除或覆盖原 SQLite 文件。
- PostgreSQL 切换和 SQLite 回滚均有可执行步骤。
- 文档表述与已提交实现和测试一致。

---

## 10. 预计文件变更范围

以下是实施阶段的预计范围，不代表这些文件已经修改：

| 文件/目录 | 计划变更 |
|---|---|
| `pyproject.toml` | 增加 PostgreSQL、连接池和迁移依赖 |
| `uv.lock` | 锁定新增依赖 |
| `app/config.py` | 增加后端枚举、DSN、连接池与超时配置 |
| `app/agent/runtime/checkpointer.py` | 增加 PostgreSQL Saver 或收敛至持久化工厂 |
| `app/services/run_store.py` | 增加 `PostgresRunStore`，必要时拆分模块 |
| `app/services/multi_agent_service.py` | 消费统一持久化资源，移除具体后端判断 |
| `app/main.py` | 保持 lifespan 入口，接入统一初始化失败语义 |
| `alembic.ini`、迁移目录 | 管理项目业务表版本 |
| `.env.example` | 增加无凭据占位配置 |
| PostgreSQL Compose 文件 | 本地数据库和健康检查 |
| `Makefile` | 增加数据库启动、迁移和测试命令 |
| `tests/unit/` | 配置、工厂和安全行为测试 |
| `tests/integration/` | PostgreSQL 恢复、并发、多实例和删除测试 |
| `tests/contract/` | 验证 JSON/SSE 兼容 |
| `README.md` | 配置、运行、迁移和能力边界 |
| `xzh_docs/handoff_docs/` | 实施完成后的交接记录 |

如果 `run_store.py` 因增加第二个数据库实现而职责过重，可按稳定职责拆分为 package；不得为了
展示架构提前创建大量只有一次调用的包装层。

---

## 11. 测试矩阵

### 11.1 单元与契约测试

| 测试项 | memory | SQLite | PostgreSQL |
|---|---:|---:|---:|
| 后端配置校验 | 是 | 是 | 是 |
| RunStore Protocol 行为 | 是 | 是 | 是 |
| 空 run/thread/trace 查询 | 是 | 是 | 是 |
| 重复保存同一 run | 是 | 是 | 是 |
| event 顺序 | 是 | 是 | 是 |
| metrics 结果 | 是 | 是 | 是 |
| thread 删除 | 是 | 是 | 是 |
| API JSON 契约 | 是 | 是 | 是 |
| SSE 单一终态 | 是 | 是 | 是 |

PostgreSQL 测试可以复用同一套后端无关契约，但必须显式标记为集成测试。

### 11.2 PostgreSQL 专项集成测试

至少包括：

1. Checkpointer 关闭连接池并重连后恢复状态。
2. RunStore 重连后恢复 thread、run、events、trace 和 metrics。
3. 两个独立 Service 实例共享同一个 PostgreSQL。
4. 相同公开 `thread_id` 下不同 `run_id` 的图状态隔离。
5. 多个 run 并发保存时不丢失或交叉 events。
6. 同一 `run_id` 重复保存符合幂等契约。
7. 保存中途异常使整个事务回滚。
8. 删除 run 后 events 外键级联生效。
9. 清理 thread 后对应 Checkpoint 被删除。
10. Checkpoint 删除失败被真实报告且不会伪造成功。
11. 数据库不可达、认证失败、Schema 缺失时启动明确失败。
12. 错误消息和日志不包含完整 DSN 或凭据。
13. 客户端提前断开 SSE 时，现有取消语义不被存储改造破坏。
14. 正常、partial、failed 三种结果均能持久化和恢复。

### 11.3 建议检查命令

实施过程中先运行最小相关测试，再执行所有可行检查：

```bash
pytest tests/integration/test_persistence_and_observability.py
pytest tests/contract/test_agent_api.py
make test-quick
make lint
make type-check
make test
```

PostgreSQL 集成测试需要独立命令或 marker，并在文档中说明如何启动测试数据库。普通单元测试
不得要求真实 DashScope Key、Milvus 或在线 MCP Server。

本计划不预设任何吞吐、延迟或并发数字。只有提交压测场景、执行命令、环境参数和结果后，才可
引用量化指标。

---

## 12. SQLite 历史数据迁移

### 12.1 RunStore 数据

`agent_runs` 和 `agent_events` 可通过一次性迁移命令复制到 PostgreSQL：

1. 进入维护窗口，停止新 run 写入。
2. 备份现有 SQLite 文件。
3. 以只读方式打开 SQLite。
4. 按稳定顺序、分批读取 runs 和 events。
5. 在 PostgreSQL 中按 run 使用事务 upsert。
6. 校验 run 数量、event 数量和每个 run 的事件顺序。
7. 抽样使用 Pydantic 反序列化响应、事件和指标。
8. 生成不包含消息正文和敏感数据的迁移摘要。
9. 校验通过后切换持久化后端。

迁移工具必须幂等。失败时保留 SQLite 原文件，不执行自动删除。

### 12.2 LangGraph Checkpoint

不直接复制 SQLite Checkpoint 内部表到 PostgreSQL，原因包括：

- 两种 Saver 的内部 Schema 由不同包维护。
- 序列化内容和迁移版本可能不同。
- 表级复制无法证明恢复语义正确。
- 当前顶层状态以单次 run 隔离，历史 run 多数已经终止。

推荐策略：

- 切换前停止接收新 run。
- 等待正在执行的 run 完成或明确终止。
- 只迁移公开 RunStore 历史记录。
- 新 run 在 PostgreSQL 中建立新 Checkpoint。
- 保留原 SQLite 文件作为历史和回滚依据。

如果未来必须恢复大量正在中断点等待的任务，需要另立计划设计应用层状态导出、版本校验和
恢复演练，不在本次迁移中推断可行。

---

## 13. 发布与回滚

### 13.1 发布前检查

- 所有数据库迁移已在空库和已有测试库验证。
- PostgreSQL 备份策略已明确。
- 应用账号只拥有运行所需权限。
- Checkpoint Schema 和业务 Schema 已初始化。
- PostgreSQL 集成测试通过。
- API/SSE 契约测试通过。
- SQLite 文件已备份。
- 日志和错误清洗测试通过。

### 13.2 推荐切换步骤

1. 暂停新请求进入。
2. 等待现有 run 进入明确终态。
3. 备份 SQLite 文件。
4. 初始化 PostgreSQL Schema。
5. 迁移并校验 RunStore 历史数据。
6. 将后端配置切换为 `postgres`。
7. 启动单个应用实例并执行健康检查。
8. 验证 run、stream、thread、trace、metrics 和 delete 接口。
9. 启动第二实例并验证共享读取。
10. 恢复流量并观察数据库连接、错误率和写入失败。

### 13.3 回滚步骤

如果切换验证失败：

1. 再次暂停新请求。
2. 停止 PostgreSQL 模式应用实例。
3. 将配置切回 `sqlite`。
4. 使用切换前保留的 SQLite 文件启动。
5. 执行 API 和 SSE 冒烟测试。
6. 记录 PostgreSQL 切换期间已经产生的 run。

本计划不使用双写。双写会引入提交顺序、部分失败和回放冲突，当前阶段收益不足。若 PostgreSQL
模式已接收真实新写入，回滚到旧 SQLite 后这些新增数据不会自动存在，必须通过维护窗口和迁移
记录明确处理。

---

## 14. 可观测性与故障语义

建议日志包含：

- persistence backend。
- 操作名称，例如 startup、save、get_trace、delete_thread。
- `run_id`、`thread_id`（适用时）。
- 耗时和成功/失败状态。
- 清洗后的异常类别。

不得记录：

- 完整数据库 DSN。
- 密码或凭据。
- 完整请求消息或文档内容。
- 未限制的响应 JSON 或 event 内容。
- SQL 参数中的用户数据。
- 原始异常栈到浏览器。

故障原则：

- PostgreSQL 是选定后端时的关键依赖，启动失败不得静默降级。
- 单次持久化失败必须让调用边界感知，不能返回已持久化的假象。
- 流式请求发生持久化失败时，必须继续遵守单一终态规则，并明确设计终态与记录失败的关系。
- 完整异常仅在服务端记录，公共错误使用稳定、清洗后的描述。

---

## 15. 风险与控制措施

| 风险 | 影响 | 控制措施 |
|---|---|---|
| 只迁移 RunStore，遗漏 Checkpoint | 多实例状态仍不共享 | 持久化工厂成对返回资源，增加双实例测试 |
| 多实例并发执行 Schema 初始化 | 启动锁或迁移冲突 | 独立初始化命令，应用启动只验证 |
| DSN 出现在日志 | 凭据泄露 | 配置与异常清洗测试 |
| SQLite upsert 语义未固定 | PostgreSQL 行为不一致 | 先补基线契约测试 |
| run 与 events 非原子写入 | Trace 不完整 | 单事务保存和故障注入测试 |
| Checkpoint 删除部分失败 | 孤立状态或错误成功响应 | 幂等删除、失败可见、残留清理策略 |
| 连接池配置不合理 | 连接耗尽或资源浪费 | 有界配置、监控、压测后调整 |
| 直接复制 Checkpoint 表 | 无法恢复或版本不兼容 | 不做表级复制，新 run 建立新状态 |
| PostgreSQL 迁移扩大到旧链路 | 范围失控、兼容风险 | 第一阶段只覆盖 Multi-Agent 顶层链路 |
| 文档提前宣称生产能力 | 项目表述失真 | 完成测试前始终标注“计划中” |

---

## 16. 验收标准

满足以下全部条件，才可认为第一阶段 PostgreSQL 改造完成：

- [x] `memory`、`sqlite`、`postgres` 均为受类型约束的可选后端。
- [x] SQLite 仍是可运行的本地开发后端。
- [x] PostgreSQL 模式下顶层 Checkpointer 与 RunStore 都不使用本地 SQLite。
- [x] PostgreSQL 资源在 FastAPI lifespan 中打开和关闭。
- [x] PostgreSQL 连接使用有界异步连接池。
- [x] 项目业务表存在版本化迁移。
- [x] LangGraph Checkpoint Schema 由官方包初始化和管理。
- [x] run 与 events 在同一事务内保存。
- [x] Pydantic 数据校验和公共错误清洗保持有效。
- [x] 两个独立应用实例能够共享运行记录和顶层 Checkpoint。
- [x] thread、run 和 Checkpoint 隔离测试通过。
- [x] 重启恢复、并发写入、删除和失败测试通过。
- [x] API JSON 和 SSE 契约测试通过。
- [x] 每条 SSE 流仍只有一个终态事件。
- [x] PostgreSQL 不可用时不会静默切换到其他后端。
- [x] 日志、API 和文档没有泄露数据库凭据。
- [x] SQLite 历史 RunStore 迁移方案经过真实 PostgreSQL 集成测试演练。
- [x] README 与架构文档只描述已经实现并测试的能力。
- [x] `git status --short` 仅包含预期修改和用户已有工作。
- [x] 已在 `xzh_docs/handoff_docs` 创建实施交接文档并记录实际检查结果。

---

## 17. 后续演进

第一阶段稳定后，再按真实需求分别评估：

1. Task Agent 子图的持久化和中断恢复。
2. 旧 `/api/chat` 与 `/api/aiops` 兼容链路迁移。
3. Checkpoint 和 Trace 数据保留、归档与清理策略。
4. 多租户字段、权限过滤和审计策略。
5. PostgreSQL 备份、恢复和故障切换演练。
6. 基于真实负载的连接池和索引优化。
7. Redis 分布式限流、MCP 工具发现缓存、幂等键或取消信号。

Redis 只有在出现多实例共享短期状态、限流或协调需求时才引入。它不替代 PostgreSQL 中的
Checkpoint、运行审计和长期 Trace。

---

## 18. 实施顺序摘要

```text
P0 固定 SQLite 行为基线
  -> P1 配置与依赖
  -> P2 PostgreSQL RunStore 与业务 Schema
  -> P3 PostgreSQL Checkpointer 与统一生命周期
  -> P4 本地环境和多实例集成测试
  -> P5 数据迁移、文档、切换与回滚演练
```

任何阶段未达到退出条件时，不进入下一阶段，也不在 README、简历或演示材料中把对应能力
描述为已完成。
