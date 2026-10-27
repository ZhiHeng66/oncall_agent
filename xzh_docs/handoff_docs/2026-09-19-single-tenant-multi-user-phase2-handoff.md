# 单租户多用户改造：第二阶段交接

日期：2026-09-19  
分支：`multi-agents`  
状态：**第二阶段实施中，未验收，禁止宣称聊天记录已隔离。**

## 1. 已完成且已验收的第一阶段

本地认证基础已实现并已验证：

- `app/auth/`：`CurrentUser`、Argon2 密码哈希、固定 HS256 JWT 签发/校验、FastAPI 认证依赖。
- `app/api/auth.py`：`POST /api/auth/token`、`GET /api/auth/me`。
- `app/services/user_store.py` 与 `migrations/versions/20260919_0003_users.py`：PostgreSQL 用户目录。
- `app/cli/users.py`：`create-admin`、`create-user`、`disable-user`；密码经交互式 `getpass` 输入且不输出。
- 认证默认关闭（`AUTH_ENABLED=false`），启用时要求 PostgreSQL 和至少 32 位、非占位的 JWT Secret。
- 已通过认证单元/契约测试、真实临时 PostgreSQL 空库迁移与用户存储测试、Ruff、格式检查、mypy，以及新增认证模块的 Bandit。

注意：仓库级 `make security` 会因既有 `app/config.py` 中 `host = "0.0.0.0"` 的 Bandit B104 告警失败；这不是认证模块引入的问题。

## 2. 第二阶段已开始的修改（未形成闭环）

以下文件已被修改或新增，但目前不能视为完成实现：

- `app/models/agent.py`
  - `AgentRunRequest` 已移除 `user_id` 并设置 `extra="forbid"`，客户端伪造 `user_id` 会被拒绝。
  - `thread_id` 当前以空字符串代表“由服务端创建”；建议下一位 Codex 改为更清晰的可选输入模型或通过 validator 明确默认行为。
- `app/agent/graph.py`
  - 顶层 Checkpoint key 已改为 `user_id:thread_id:run_id`。
  - `delete_run_state()` 签名已改为 `(user_id, thread_id, run_id)`。
- `app/api/agent.py`
  - 统一 Agent 路由已注入 `get_current_user`。
  - **仍不安全**：Run/Trace/Metrics/Thread 查询目前只注入身份但没有把身份传入 Service/Store 查询条件，尚未完成所有权过滤。
  - SSE 中 `LookupError` 发生在生成器内部，尚未统一转换为启动前 404；需在创建事件流前先解析/校验 Thread。
- `app/services/thread_store.py`
  - 新增 `AgentThread`、`InMemoryThreadStore` 与 `PostgresThreadStore`，仅实现 create/get_owned/delete_owned。
  - 尚缺 `setup`、分页 `list_owned`、`touch_owned`，也没有测试。
- `app/services/persistence.py`
  - 已在 `PersistenceResources` 中加入 `thread_store`，PostgreSQL 使用 `PostgresThreadStore`。
- `app/services/multi_agent_service.py`
  - `run()` 和 `stream()` 开始接收 `owner_user_id`，并通过 `_resolve_request()` 创建或验证 Thread。
  - **尚不安全**：RunStore、WorkingMemoryStore 和查询/删除路径仍没有 owner 参数；工作记忆更新中曾使用旧 `request.user_id`，当前改为占位值，必须在后续改造中彻底修正。
- `migrations/versions/20260919_0004_user_owned_threads.py`
  - 创建 `agent_threads`。
  - 为 `agent_runs` 增加 `owner_user_id`，并把旧记录归入禁用、不可登录的 `legacy-system` 用户。
  - 此 migration 尚未在真实 PostgreSQL 上运行；未处理 `agent_working_memories.owner_user_id` 的历史数据和外键/类型一致性，不能执行后宣称完成。

## 3. 当前风险与禁止事项

1. 不要运行 `alembic upgrade head` 或 `python -m app.cli.database` 在共享数据库直接应用 `20260919_0004`，先修完 migration 并在临时库验证升级/回滚。
2. 当前工作区已有大量用户修改和其他 Multi-Agent 改造；不得 reset、checkout、清理未跟踪文件或覆盖无关 diff。
3. `app/api/chat.py`、`app/api/aiops.py` 仍可绕过第二阶段认证/所有权边界，必须在第二阶段结束前完成改造。
4. 不能为兼容旧调用而恢复客户端 `user_id`，也不能先按 `run_id/thread_id` 读取后再在 Service 层比较所有者。
5. 不读取、打印或复制 `.env`。

## 4. 建议的后续执行顺序

1. **先修复当前中间态并建立测试基线**
   - 运行 `make lint`、`make type-check`，并修复本轮签名变化引入的所有调用方、Fake 和测试错误。
   - 为 `AgentRunRequest` 增加“省略 thread_id 创建新 Thread、提交 user_id 返回 422”的契约测试。
2. **完成所有权存储契约**
   - 将 `RunStore` 的 save/get_run/get_trace/get_thread/get_recent_thread/delete_thread/metrics 全部改为带 `owner_user_id: UUID`。
   - Memory、SQLite、PostgreSQL 三种实现都必须在索引或 SQL `WHERE` 内做所有者过滤；PostgreSQL 以 `run_id + owner_user_id` 读取，SQLite 增加 owner 列和迁移逻辑。
   - 完成 ThreadStore 的列表分页、touch、delete，并由 Service 调用。
3. **完成工作记忆与 Checkpoint**
   - `WorkingMemoryStore.load/delete_thread/compare_and_swap` 均接收 owner；SQL 包含 owner 条件。
   - `_prepare_memory()` 在构造 Context Pack 前校验所有者；`_update_working_memory()` 从可信 `owner_user_id` 传入 Reducer，而不是请求体。
   - Task 子图 Checkpoint key 也加入 `user_id`，并修改相关协议与 Fake。
4. **完成 API 与兼容接口**
   - 所有 `/api/agent/*` 查询、删除、metrics 向 Service 传 `CurrentUser.user_id`；无权和不存在统一 404。
   - `/api/chat*` 的 `Id` 只作 thread_id，强制认证；`/api/aiops` 同样认证，不能共享默认会话。
   - 普通管理员也只能通过普通接口读取自己的会话。
5. **修复并验证 migration**
   - 将 `agent_working_memories` 历史记录归入 legacy 用户，并确认 UUID/text 类型与现有表兼容。
   - 用隔离 PostgreSQL 做空库、含旧 `agent_runs` 与含旧工作记忆三种升级测试。
6. **补齐验收测试与文档**
   - 两普通用户 A/B 与管理员：线程、JSON/SSE、Run、Trace、历史、删除、Metrics、工作记忆、Checkpoint 均覆盖越权 404。
   - 通过 Memory、SQLite、PostgreSQL 的所有权契约测试；随后更新计划、架构文档和 AGENTS.md 的真实能力描述。

## 5. 关键验证命令

```bash
make lint
make type-check

.venv/bin/python -m pytest tests/contract tests/unit tests/integration -v

# 使用显式、隔离的 TEST_POSTGRES_URL；不要读取 .env。
TEST_POSTGRES_URL='postgresql://...' \
  .venv/bin/python -m pytest tests/integration/test_postgres_users.py -v
```

验收完成前，不得把计划文档第二阶段的复选框改为完成，也不得声明“后端已实现单租户多用户隔离”。
