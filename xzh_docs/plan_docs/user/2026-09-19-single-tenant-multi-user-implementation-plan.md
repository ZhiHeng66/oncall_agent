# 单租户多用户与个人会话隔离实施计划

> 项目：企业知识服务多智能体协同平台
>
> 文档日期：2026-09-19
>
> 文档状态：第一至三阶段已完成验收
>
> 适用分支：`multi-agents`
>
> 实施范围：统一 Agent API、Chat/AIOps 兼容接口、RunStore、工作记忆和静态前端
>
> 目标部署形态：单个公司或团队，多名用户共享应用与知识库，个人会话互相隔离

## 1. 实施结论

本次采用“单租户、多用户”方案。系统不引入 `tenant_id`，所有用户共享以下企业级能力：

- 同一个 FastAPI 应用。
- 同一个 PostgreSQL 数据库。
- 同一个 Milvus 企业知识库。
- 同一组只读 MCP Server 和工具配置。
- 同一套 Supervisor、Knowledge、Data、Task、Reviewer Agent。

以下数据按认证用户隔离：

- 会话列表和会话标题。
- 用户问题与 Agent 回答。
- Run、Trace 和个人运行指标。
- LangGraph 顶层 Checkpoint。
- 跨轮工作记忆。
- 浏览器中的当前会话状态和本地缓存。

目标身份层级为：

```text
user_id
  `-- thread_id
        `-- run_id
```

`user_id` 必须来自服务端验证成功的登录凭证，不再接受请求体中的任意用户标识。
`thread_id` 和 `run_id` 由服务端生成或在服务端完成所有权校验。知道其他用户的 ID 不能成为
访问其数据的依据。

正式的多用户部署以 PostgreSQL 为前提。现有 Memory 和 SQLite RunStore 继续用于单元测试、协议
回归和显式本地开发，但第一版不为它们实现可持久化的用户目录，也不得据此宣称完整多用户部署。

本计划分三个阶段推进：

1. 建立用户、密码、JWT 和认证依赖，不立即宣称完成数据隔离。
2. 建立 Thread 所有权并贯穿 Run、Trace、Checkpoint 和工作记忆，完成后端隔离闭环。
3. 接入前端登录和个人历史列表，完成旧接口兼容、安全回归和文档收口。

每个阶段只有在对应验收条件全部满足后才能进入下一阶段。不得将后续阶段的缺口写成当前已实现能力。

---

## 2. 当前源码基线与风险

### 2.1 当前已有基础

- `app/models/agent.py` 的 `AgentRunRequest` 已区分 `user_id`、`thread_id` 和消息，但
  `user_id` 目前由客户端提供。
- `app/agent/state.py` 和 `app/agent/graph.py` 已在顶层状态中携带 `user_id`，Agent 编排本身
  不需要大规模重写。
- `app/services/multi_agent_service.py` 已统一处理 JSON、SSE、公开 Run、Trace、Metrics 和
  工作记忆更新。
- `app/services/run_store.py` 已提供 Memory、SQLite、PostgreSQL 三种 RunStore。
- `app/services/working_memory_store.py` 已保存 `owner_user_id`，但读取和删除仍只使用
  `thread_id`。
- `app/services/persistence.py` 已在 FastAPI lifespan 中管理共享 PostgreSQL 连接池。
- `migrations/` 和 `app/cli/database.py` 已提供 Alembic 业务表升级流程。
- `static/app.js` 使用 `fetch()` 消费 JSON 和 SSE，可以添加 Bearer Token 请求头。
- 测试已经按 unit、integration、contract、evals 分层，可沿用现有 Fake 和依赖注入模式。

### 2.2 当前隔离缺口

1. API 没有登录、认证依赖和当前用户上下文。
2. 客户端可以在 `AgentRunRequest.user_id` 中声明任意用户。
3. `/api/chat` 把客户端传入的 `Id` 同时作为用户 ID 和线程 ID。
4. `agent_runs` 没有 `owner_user_id`，查询只按 `run_id` 或 `thread_id`。
5. 当前没有独立的 `agent_threads` 表，无法集中验证线程归属。
6. Run、Trace、Thread、Delete 和 Metrics 接口均未执行用户授权。
7. 工作记忆虽然保存所有者，但 `load()`、`delete_thread()` 只按 `thread_id`；错误用户可能在
   后续校验失败前读到他人的上下文。
8. 顶层 Checkpoint Key 当前为 `thread_id:run_id`，缺少已认证用户维度。
9. 前端没有登录状态，历史数据和本地缓存没有按用户命名空间隔离。
10. 测试覆盖运行隔离和持久化，但没有覆盖跨用户越权。

### 2.3 当前最小攻击面

在现有实现中，只要知道或猜到其他人的 `run_id`、`thread_id`，调用方可能尝试：

- 查询运行结果。
- 查询完整 Trace。
- 查询线程历史。
- 删除线程及关联状态。
- 复用同名线程加载他人的工作记忆。
- 通过全局 Metrics 看到不属于自己的聚合数据。

因此只增加登录页或 JWT 还不构成用户隔离。认证身份、服务层授权和 Store 查询条件必须形成闭环。

---

## 3. 目标、非目标与安全不变量

### 3.1 目标

1. 用户必须登录后才能访问业务 API。
2. 密码只保存 Argon2 哈希，不保存或记录明文密码。
3. Access Token 使用服务端配置的密钥签名，并验证算法、`sub` 和过期时间。
4. 业务代码通过 `CurrentUser` 获取可信用户身份。
5. 客户端不能通过请求体覆盖 `user_id`。
6. 新会话由服务端创建，并记录唯一所有者。
7. Run、Trace、Thread、Checkpoint 和工作记忆与所有者保持一致。
8. 普通用户只能列出、查询和删除自己的会话。
9. 普通用户只能看到自己的 Metrics；管理员可以查看全局聚合指标，但默认不能查看他人聊天内容。
10. 统一 Agent API、Chat 兼容接口和 AIOps 兼容接口使用同一认证边界。
11. JSON 与 SSE 保持现有响应和单终态语义。
12. Memory、SQLite、PostgreSQL RunStore 在测试范围内保持所有权协议一致，正式用户目录和共享
    多用户部署使用 PostgreSQL。
13. 应用重启后，PostgreSQL 中的用户、会话、运行记录和工作记忆仍可恢复。
14. 旧数据以安全、可追踪的方式迁移或隔离，不静默归属于任意真实用户。

### 3.2 非目标

本计划不实施：

- `tenant_id`、组织表或跨公司隔离。
- Keycloak、Entra ID、Okta、LDAP 或企业 SSO。
- Milvus Database、Collection、Partition 或 Partition Key 多租户改造。
- 每个用户独立知识库；企业知识文档继续共享。
- 每个用户或租户独立 MCP 凭据。
- OAuth 第三方登录、MFA、Passkey 或短信验证码。
- Refresh Token、Token 黑名单、单点登出和设备管理。
- PostgreSQL Row Level Security；第一版由应用层强制所有权过滤。
- 管理员查看员工聊天内容的审计功能。
- 用户之间共享会话、会话成员 ACL 或协作空间。
- 用户注册开放给公网；用户由管理员 CLI 创建。
- 宣称已经达到生产级 IAM、合规审计或多租户安全等级。

### 3.3 必须始终成立的安全不变量

- API 中的可信 `user_id` 只能来自 `CurrentUser`。
- `username`、邮箱、显示名称等可变字段不能作为数据所有权主键。
- Thread 在创建时绑定一个 `owner_user_id`，普通接口不得转移所有权。
- Store 的用户数据方法必须接收 `owner_user_id`，不能依赖调用方先查询后自行比较。
- 查询条件必须在数据库语句内包含所有者，避免“先查出数据再判断”的泄漏窗口。
- 无权访问与资源不存在统一返回 404，避免通过状态码枚举他人资源。
- 工作记忆必须在构造 Agent Context Pack 之前完成所有权过滤。
- 删除操作必须按同一所有者范围执行，并返回实际删除数量。
- 日志可以记录内部 `user_id`、`thread_id` 和 `run_id`，不得记录密码、Token 或完整聊天内容。
- 测试不得读取本地 `.env` 或使用真实开发者凭据。

---

## 4. 目标技术栈和模块边界

### 4.1 技术选型

| 能力 | 第一版技术 | 说明 |
|---|---|---|
| API 安全依赖 | FastAPI `Depends` / `Security` | 注入并校验当前用户 |
| 密码哈希 | `pwdlib[argon2]` | 只保存安全哈希 |
| Token | `PyJWT[crypto]` | 签发和验证 Bearer JWT |
| 用户与会话存储 | PostgreSQL + psycopg | 复用现有连接池 |
| Schema 迁移 | Alembic | 延续现有迁移方式 |
| 公共契约 | Pydantic | CurrentUser、Token、Thread API |
| 浏览器调用 | 原生 JavaScript `fetch()` | JSON 和 SSE 都携带 Authorization |
| 测试 | pytest、FastAPI TestClient、Fake Store | 不依赖真实 LLM/Milvus/MCP |

### 4.2 新增模块建议

```text
app/
├── auth/
│   ├── __init__.py
│   ├── contracts.py       CurrentUser、AccessTokenClaims
│   ├── password.py        Argon2 哈希与验证
│   ├── tokens.py          JWT 签发与验证
│   └── dependencies.py    get_current_user、角色检查
├── api/
│   └── auth.py            /api/auth/token、/api/auth/me
├── services/
│   ├── user_store.py      用户读取、创建、禁用和登录查询
│   └── thread_store.py    会话创建、归属、列表和更新时间
└── cli/
    └── users.py           管理员创建和用户管理命令
```

不要为每个路由重复解析 JWT。API 层只依赖统一的 `get_current_user()`，服务层只接收
`CurrentUser` 或不可变的 `user_id`，Agent 节点不感知密码和 Token。

### 4.3 核心身份契约

建议模型：

```python
UserRole = Literal["user", "admin"]


class CurrentUser(BaseModel):
    user_id: UUID
    username: str
    role: UserRole


class AccessTokenClaims(BaseModel):
    sub: UUID
    exp: datetime
    iat: datetime
    token_type: Literal["access"] = "access"
```

JWT 只保存最小身份信息。每个请求验证 Token 后仍从 UserStore 检查用户是否存在且启用，确保禁用
账号不必等待 Token 自然过期。

### 4.4 目标数据库模型

建议新增：

```text
users
├── user_id          UUID PRIMARY KEY
├── username         TEXT NOT NULL UNIQUE
├── password_hash    TEXT NOT NULL
├── role             TEXT NOT NULL
├── is_active        BOOLEAN NOT NULL DEFAULT TRUE
├── created_at       TIMESTAMPTZ NOT NULL
└── updated_at       TIMESTAMPTZ NOT NULL

agent_threads
├── thread_id        TEXT PRIMARY KEY
├── owner_user_id    UUID NOT NULL REFERENCES users(user_id)
├── title            TEXT NOT NULL DEFAULT ''
├── created_at       TIMESTAMPTZ NOT NULL
├── updated_at       TIMESTAMPTZ NOT NULL
└── archived_at      TIMESTAMPTZ NULL
```

`thread_id` 第一版继续使用 TEXT，以兼容现有 API、Checkpoint 和历史数据；所有新 Thread 使用
服务端生成的 UUID 字符串。

修改现有表：

```text
agent_runs
├── owner_user_id    UUID NOT NULL REFERENCES users(user_id)
└── thread_id        TEXT NOT NULL REFERENCES agent_threads(thread_id)

agent_working_memories
├── owner_user_id    UUID NOT NULL REFERENCES users(user_id)
└── thread_id        TEXT NOT NULL REFERENCES agent_threads(thread_id)
```

建议索引：

```sql
CREATE INDEX idx_agent_threads_owner_updated
    ON agent_threads(owner_user_id, updated_at DESC, thread_id);

CREATE INDEX idx_agent_runs_owner_thread_created
    ON agent_runs(owner_user_id, thread_id, created_at, run_id);
```

`agent_events` 继续通过 `run_id` 关联 `agent_runs`，无需重复保存用户 ID。读取 Trace 时必须从
`agent_runs` 以 `run_id + owner_user_id` 开始查询，再读取关联事件。

### 4.5 API 目标形态

认证接口：

```text
POST /api/auth/token
GET  /api/auth/me
```

Thread 接口：

```text
POST   /api/agent/threads
GET    /api/agent/threads
GET    /api/agent/threads/{thread_id}
DELETE /api/agent/threads/{thread_id}
```

Agent 请求从：

```json
{
  "user_id": "user-001",
  "thread_id": "thread-001",
  "message": "问题"
}
```

迁移为：

```json
{
  "thread_id": "可选，继续已有会话时传入",
  "message": "问题"
}
```

若不传 `thread_id`，服务端创建 Thread 并在响应与 SSE 事件中返回最终 `thread_id`；若传入，服务端
必须先验证该 Thread 属于当前用户。

---

## 5. 三阶段总览

| 阶段 | 目标 | 阶段完成后可声明的能力 | 不得提前声明 |
|---|---|---|---|
| 第一阶段 | 认证与用户基础 | 支持本地账号、密码哈希、JWT 身份验证 | 聊天记录已按用户隔离 |
| 第二阶段 | 后端数据所有权闭环 | API、Run、Trace、Checkpoint、工作记忆按用户隔离 | 前端已完整支持多用户 |
| 第三阶段 | 前端接入与稳定性收口 | 用户可登录并只看到自己的历史，兼容接口和文档完成迁移 | 多租户、企业 SSO、生产级 IAM |

每个阶段都应独立提交测试证据和交接文档。交接文档统一放在 `xzh_docs/handoff_docs/`。

---

## 6. 第一阶段：认证与用户基础

### 6.1 阶段目标

建立可信身份来源，为第二阶段的数据所有权改造提供稳定契约。本阶段是内部基础阶段，完成后仍不得
对外宣称个人聊天记录已经隔离。

### 6.2 实施内容

#### A. 依赖与配置

在 `pyproject.toml` 增加：

```text
PyJWT[crypto]
pwdlib[argon2]
```

在 `app/config.py` 增加：

```text
JWT_SECRET_KEY
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
```

要求：

- `JWT_SECRET_KEY` 无安全默认值；启用认证时为空必须启动失败。
- 算法只允许配置中的固定值，解码时不得信任 Token Header 自由选择算法。
- `.env.example` 只提供占位符，不生成或提交真实密钥。
- 不在日志和异常信息中输出 Token 或 Secret。

#### B. 用户表与 UserStore

新增 Alembic migration 创建 `users`。新增 `UserStore` 协议及 PostgreSQL 实现，至少支持：

```python
async def get_by_id(user_id: UUID) -> UserRecord | None: ...
async def get_by_username(username: str) -> UserRecord | None: ...
async def create(user: NewUser) -> UserRecord: ...
async def set_active(user_id: UUID, is_active: bool) -> None: ...
```

单元测试使用 Fake UserStore，不依赖真实 PostgreSQL。

#### C. 密码和 Token

实现：

- Argon2 密码哈希。
- 密码验证。
- Access Token 签发。
- 固定算法验证。
- `sub`、`iat`、`exp` 和 `token_type` 校验。
- 无效、过期、格式错误 Token 统一返回 401。

认证错误不得区分“用户名不存在”和“密码错误”，避免账号枚举。

#### D. FastAPI 认证依赖

实现 `get_current_user()`：

1. 从 `Authorization: Bearer` 读取 Token。
2. 验证签名和 Claims。
3. 按 `sub` 从 UserStore 加载用户。
4. 拒绝不存在或已禁用用户。
5. 返回 `CurrentUser`。

实现可复用的 `require_role("admin")`，但本阶段只用于用户管理入口，不引入复杂权限框架。

#### E. 认证 API 与管理员 CLI

新增：

- `POST /api/auth/token`：用户名和密码换取 Access Token。
- `GET /api/auth/me`：返回当前用户的公开身份信息。
- `python -m app.cli.users create-admin`：显式创建首个管理员。
- 可选 `create-user`、`disable-user` 子命令。

CLI 使用交互输入或显式环境变量接收密码，不把密码写入命令日志、配置文件和迁移脚本。

### 6.3 主要修改文件

```text
pyproject.toml
.env.example
app/config.py
app/main.py
app/auth/*
app/api/auth.py
app/services/user_store.py
app/cli/users.py
migrations/versions/20260919_0003_users.py
tests/unit/test_passwords.py
tests/unit/test_tokens.py
tests/unit/test_auth_dependencies.py
tests/contract/test_auth_api.py
tests/integration/test_postgres_users.py
```

### 6.4 测试要求

必须覆盖：

- 相同密码不会依赖明文存储即可验证。
- 错误密码、未知用户、禁用用户均认证失败。
- Token 过期、签名错误、算法错误、缺少 `sub` 均返回 401。
- `/api/auth/me` 返回 Token 对应用户，不接受客户端用户 ID。
- 用户名唯一约束和并发重复创建行为明确。
- CLI 不把密码打印到标准输出或日志。
- 单元和契约测试不读取本地 `.env`。

### 6.5 第一阶段验收条件

以下条件必须全部满足：

- [x] `users` migration 可在空 PostgreSQL 数据库成功升级。
- [x] 应用配置缺少 JWT Secret 时明确失败，不回退到硬编码密钥。
- [x] 管理员 CLI 能创建管理员，重复用户名被明确拒绝。
- [x] 正确账号可取得有效 Access Token。
- [x] `/api/auth/me` 能返回当前用户，未登录和无效 Token 返回 401。
- [x] 禁用用户即使持有未过期 Token也不能访问 `/api/auth/me`。
- [x] 密码、密码哈希、JWT 和 Secret 不出现在日志、SSE 或公共错误中。
- [x] 新增认证单元测试、契约测试全部通过。
- [x] PostgreSQL 用户存储集成测试在隔离数据库中通过。
- [x] Ruff、格式检查、mypy 和 Bandit 对新增模块通过。
- [x] 架构文档明确标注“认证已实现，用户数据隔离仍待第二阶段完成”。

验收记录（2026-09-19）：在临时、隔离的 PostgreSQL 17 数据库中执行
`tests/integration/test_postgres_users.py`，验证 Alembic 从空库升级并完成用户存储读写；测试后已
删除该临时数据库容器。认证单元、契约、配置测试及新增模块的 Ruff、格式检查、mypy、Bandit 均已通过。
当前仅实现本地账户、Argon2 密码哈希、JWT 与认证 API；现有业务 API、Run、Trace、Checkpoint 和工作
记忆尚未按用户做所有权过滤，必须在第二阶段完成前保持为未隔离状态。

### 6.6 阶段门禁与回退

- 第二阶段开始前冻结 `CurrentUser`、Token Claims 和 UserStore 协议，避免所有权改造期间反复变化。
- 第一阶段不能仅因为出现登录页或 JWT 就修改 README 宣称支持多用户隐私隔离。
- 若认证改造需要暂时保留现有业务 API，本地测试环境可继续使用现有请求模型；不得把该中间状态部署为
  已完成用户隔离的共享环境。
- 回退第一阶段时只回退认证入口和用户表，不修改现有 Run/Checkpoint 数据。

---

## 7. 第二阶段：后端所有权隔离闭环

### 7.1 阶段目标

让认证用户身份贯穿 Thread、Run、Trace、Metrics、Checkpoint 和工作记忆。本阶段完成后，API 层面
应具备可独立验收的个人会话隔离能力，即使前端登录页面尚未完成，也可以通过 Bearer Token 验证。

### 7.2 实施内容

#### A. Thread 领域模型与存储

新增 `agent_threads` migration、Pydantic 模型和 ThreadStore，至少支持：

```python
async def create(owner_user_id: UUID, title: str = "") -> AgentThread: ...
async def get_owned(owner_user_id: UUID, thread_id: str) -> AgentThread | None: ...
async def list_owned(owner_user_id: UUID, limit: int, cursor: str | None) -> ThreadPage: ...
async def touch_owned(owner_user_id: UUID, thread_id: str) -> bool: ...
async def delete_owned(owner_user_id: UUID, thread_id: str) -> bool: ...
```

列表接口必须分页并设置服务端最大 `limit`，不能一次返回所有历史。

#### B. Agent 请求契约

从 `AgentRunRequest` 删除 `user_id`，将 `thread_id` 改为可选：

```python
class AgentRunRequest(BaseModel):
    thread_id: str | None = None
    message: str
```

行为：

- 不传 `thread_id`：服务端创建新 Thread。
- 传入 `thread_id`：必须通过 `get_owned()` 验证所有权。
- 不存在或不属于当前用户：统一返回 404。
- Service 调用图时使用 `current_user.user_id`，不能读取请求体用户 ID。

#### C. RunStore 所有权

修改 RunStore 协议与三个实现，使所有用户数据操作带 `owner_user_id`：

```python
async def save(owner_user_id, request_message, response, events, metrics) -> None: ...
async def get_run(owner_user_id, run_id) -> AgentRunResponse | None: ...
async def get_trace(owner_user_id, run_id) -> AgentRunTraceResponse | None: ...
async def get_thread(owner_user_id, thread_id) -> AgentThreadResponse: ...
async def get_recent_thread(owner_user_id, thread_id, limit) -> list[PublicConversationTurn]: ...
async def delete_thread(owner_user_id, thread_id) -> list[str]: ...
async def metrics(owner_user_id) -> AgentMetricsResponse: ...
```

PostgreSQL 和 SQLite 查询必须在 SQL `WHERE` 中包含所有者；MemoryRunStore 的内存索引也必须按用户
隔离。禁止先按 ID 取出完整对象再在 Service 中判断。

#### D. 工作记忆所有权

修改 WorkingMemoryStore：

```python
async def load(owner_user_id, thread_id) -> WorkingMemoryContext | None: ...
async def create(context) -> bool: ...
async def compare_and_swap(owner_user_id, context, expected_version) -> bool: ...
async def delete_thread(owner_user_id, thread_id) -> None: ...
```

必须在 `_prepare_memory()` 构造 `MemoryContextView` 前完成所有权过滤。Store 返回的 Context 仍需由
Pydantic 校验 `owner_user_id` 与调用身份一致，形成存储层和契约层双重约束。

#### E. Checkpoint 隔离与删除

顶层 Checkpoint Key 调整为：

```text
user_id:thread_id:run_id
```

Task Agent 内部 Key 调整为至少：

```text
user_id:thread_id:run_id:task_id
```

若 Task 子图仍使用进程内 Checkpointer，也应保持键语义一致。删除线程时只能删除当前用户拥有的
Run 对应 Checkpoint，不能按未经授权的 `thread_id` 直接执行删除。

#### F. API 授权与角色规则

保护：

- `/api/agent/run`
- `/api/agent/run_stream`
- `/api/agent/runs/{run_id}`
- `/api/agent/runs/{run_id}/trace`
- `/api/agent/threads*`
- `/api/agent/metrics`
- `/api/chat*`
- `/api/aiops`

普通用户 Metrics 只聚合自己的 Run。管理员可以增加独立的全局 Metrics 接口或查询参数，但必须有
明确角色检查；管理员默认也不能通过普通 Run/Thread API 查看其他用户聊天内容。

旧 `/api/chat` 中的 `Id` 只作为兼容 `thread_id`，不再表示用户。所有者由 `CurrentUser` 决定。

#### G. 历史数据迁移

现有 Run 没有可信所有者，禁止自动分配给第一个真实用户。推荐迁移策略：

1. 创建一个禁用且不可登录的 `legacy-system` 用户。
2. 为现有唯一 `thread_id` 创建对应的 `agent_threads`。
3. 将旧 Run 和工作记忆归入 `legacy-system`。
4. 普通用户和管理员的普通会话 API都看不到这些隔离数据。
5. 如需保留演示数据，未来通过专用离线工具显式认领；本计划不实现在线认领。

迁移顺序采用“新增可空列 -> 回填 -> 建外键/索引 -> 改为 NOT NULL”，避免直接破坏现有数据。

### 7.3 主要修改文件

```text
app/models/agent.py
app/models/request.py
app/api/agent.py
app/api/chat.py
app/api/aiops.py
app/services/multi_agent_service.py
app/services/run_store.py
app/services/working_memory_store.py
app/services/thread_store.py
app/services/persistence.py
app/agent/state.py
app/agent/graph.py
app/agent/specialists/task/graph.py
app/agent/memory/contracts.py
migrations/versions/<revision>_add_user_owned_threads.py
tests/fakes/*
tests/unit/test_thread_store.py
tests/unit/test_working_memory_store.py
tests/integration/test_multi_agent_service.py
tests/integration/test_persistence_and_observability.py
tests/integration/test_postgres_persistence.py
tests/contract/test_agent_api.py
tests/contract/test_legacy_api_contracts.py
```

### 7.4 测试要求

使用两个普通用户 A、B 和一个管理员覆盖：

- A 创建 Thread，B 的列表中不可见。
- B 使用 A 的 `thread_id` 请求 JSON 或 SSE，返回 404，图不得启动。
- B 使用 A 的 `run_id` 查询 Run 或 Trace，返回 404。
- B 删除 A 的 Thread，返回 404，A 的数据保持不变。
- A、B 使用相同的客户端遗留 Session ID 时不会共享历史或工作记忆。
- 伪造 JSON `user_id` 字段被拒绝或忽略，不改变 CurrentUser。
- 工作记忆加载失败或所有者不匹配时不进入 Agent Context Pack。
- 普通用户 Metrics 只计算自己的 Run。
- 管理员普通 Thread API 仍不能直接读取其他用户内容。
- Thread 删除只删除对应用户的 Run、Event、Checkpoint 和工作记忆。
- Memory、SQLite、PostgreSQL RunStore 满足同一个所有权协议测试。
- JSON 和 SSE 每个流仍只有一个终态事件。
- 两个用户并发运行时不会发生 Run、Event、Checkpoint 或 CAS 工作记忆串线。

### 7.5 第二阶段验收条件

以下条件必须全部满足：

- [ ] `agent_threads`、Run 所有者和工作记忆所有者 migration 可在空库及带旧数据的测试库升级。
- [ ] 旧数据进入禁用 `legacy-system` 所有者，未暴露给任何真实用户。
- [ ] `AgentRunRequest` 不再接受可信的客户端 `user_id`。
- [ ] 所有业务 API 都从 `CurrentUser` 获得用户身份。
- [ ] Thread 创建、继续、列表、查询和删除均执行所有权过滤。
- [ ] Run、Trace、最近历史和 Metrics 均按用户过滤。
- [ ] 工作记忆在进入 Agent 上下文前完成所有权过滤。
- [ ] 顶层 Checkpoint Key 包含用户、线程和运行标识。
- [ ] A 无法读取、继续或删除 B 的任何会话、Run、Trace 和工作记忆。
- [ ] 越权访问统一返回 404，服务端日志只记录必要的安全摘要。
- [ ] `/api/chat` 和 `/api/aiops` 不能绕过认证与线程所有权。
- [ ] Memory、SQLite、PostgreSQL 后端的所有权契约测试通过。
- [ ] PostgreSQL 隔离集成测试在独立数据库中通过。
- [ ] 现有 Supervisor、Specialist、Reviewer、SSE 和 Eval 行为没有无关回归。
- [ ] API 与架构文档更新为“后端已实现单租户多用户隔离，前端接入待第三阶段完成”。

### 7.6 阶段门禁与回退

- 第二阶段 migration 在共享环境执行前必须完成数据库备份和回滚演练。
- 切换 API 请求契约与数据库 NOT NULL 约束应在同一发布窗口完成，避免新旧实例混合写入。
- 不提供“找不到所有者时退回按 thread_id 查询”的兼容逻辑。
- 若阶段二失败，回退应用版本和 migration；不得通过临时移除所有者过滤恢复服务。

---

## 8. 第三阶段：前端接入、兼容迁移与稳定性收口

### 8.1 阶段目标

让浏览器端完整使用认证和个人会话 API，消除旧前端对任意 Session ID 和全局历史的依赖，并通过
全量安全、契约和回归测试完成项目表述收口。

### 8.2 实施内容

#### A. 登录与退出体验

修改 `static/index.html`、`static/app.js`、`static/styles.css`：

- 增加登录表单。
- 调用 `/api/auth/token` 获取 Access Token。
- 调用 `/api/auth/me` 获取当前用户。
- 所有受保护的 JSON 和 SSE `fetch()` 添加 Bearer Token。
- 收到 401 时清理认证状态并返回登录页。
- 退出时清理 Token、当前用户、当前 Thread 和页面内聊天内容。
- 页面显示当前用户名，不显示内部密码哈希或 Token。

第一版 Token 可保存在 `sessionStorage`，关闭标签页后失效。不得写入日志、URL、DOM 文本或长期
`localStorage`。后续如果需要更高等级浏览器安全，再单独规划 HttpOnly Cookie/BFF。

#### B. 个人会话列表

- 历史记录以 `GET /api/agent/threads` 为服务端事实来源。
- 新聊天由服务端创建 Thread。
- 点击历史项只请求当前用户可访问的 Thread。
- 删除操作调用受保护的 Thread DELETE 接口。
- 列表支持分页或有界加载，不一次拉取全部历史。
- 浏览器本地仅缓存展示状态；若保留非敏感缓存，Key 必须包含 `user_id`。
- 启动时清理或迁移旧的全局聊天缓存，避免同一浏览器上的后续用户看到前一用户内容。

#### C. 管理员最小能力

第一版只提供 CLI 用户管理。若增加前端管理员入口，范围限制为：

- 创建用户。
- 禁用或启用用户。
- 查看不含聊天内容的用户列表。
- 查看全局聚合 Metrics。

不得在本阶段增加管理员查看用户聊天内容的接口。

#### D. 文件上传和公共页面权限

- `/health` 和登录所需静态资源可以保持匿名。
- Agent、Chat、AIOps、历史和 Metrics 必须认证。
- 文档上传和目录索引建议限制为 `admin`。
- FastAPI `/docs` 是否开放由配置决定；共享演示环境建议关闭或加认证。

#### E. 文档与运维

同步更新：

- `README.md`：创建管理员、登录、Token 和个人历史使用方式。
- `.env.example`：JWT 配置占位符。
- `xzh_docs/architecture/`：认证边界和会话所有权时序。
- `xzh_docs/appendices/api-reference.md`：新认证和 Thread 契约。
- `AGENTS.md`：用户隔离的真实能力和后续修改入口。
- Windows 启停脚本和 Makefile：必要的用户 CLI 与迁移命令。

所有表述必须使用“单租户多用户隔离”，不得写成“多租户平台”。

### 8.3 主要修改文件

```text
static/index.html
static/app.js
static/styles.css
app/api/file.py
app/api/health.py（如需明确匿名边界）
app/config.py
README.md
.env.example
Makefile
start-windows.bat
stop-windows.bat
AGENTS.md
xzh_docs/architecture/*
xzh_docs/appendices/api-reference.md
tests/contract/test_frontend_agent_contract.py
tests/contract/test_legacy_api_contracts.py
tests/contract/test_agent_api.py
tests/integration/test_multi_user_isolation.py
```

### 8.4 测试要求

自动化测试：

- 前端所有受保护请求都携带 Authorization。
- 前端不再构造或发送任意 `user_id`。
- 前端按当前用户加载 Thread 列表。
- 401 会清理当前用户和会话状态。
- 退出登录后页面不保留上一用户聊天内容。
- 旧 Chat/AIOps 调用仍符合兼容响应格式，但需要认证。
- 文档上传权限符合角色规则。
- 全量 unit、contract、integration、eval 测试通过。

手工双用户验收：

1. 管理员创建用户 A、B。
2. A 登录并创建两条会话，刷新页面后仍能恢复。
3. A 退出，B 在同一浏览器登录。
4. B 看不到 A 的会话标题、内容和 Run。
5. B 创建会话后，A 重新登录时看不到 B 的会话。
6. 使用浏览器开发者工具把 A 的 `thread_id/run_id` 放入 B 的请求，服务端返回 404。
7. B 尝试删除 A 的 Thread，A 的数据保持存在。
8. A、B 的连续追问分别加载各自工作记忆。
9. 管理员可查看全局 Metrics，但普通用户只能查看个人 Metrics。
10. 日志中不出现密码、Token 或完整聊天内容。

### 8.5 第三阶段验收条件

以下条件必须全部满足：

- [x] 未登录用户只能访问明确列出的匿名入口。
- [x] 用户可登录、查看本人信息并安全退出。
- [x] 所有前端 Agent JSON/SSE 请求携带 Bearer Token。
- [x] 用户历史列表完全来自服务端的个人 Thread 查询。
- [x] 同一浏览器依次登录 A、B 时，B 看不到 A 的本地或服务端历史。
- [x] 前端不再发送可信 `user_id`，也不允许用户选择任意 Session 所有者。
- [x] Chat、AIOps 和文件接口的认证/角色行为与文档一致。
- [x] 双用户越权场景由 API、服务和 PostgreSQL 隔离测试覆盖，返回 404 或 403。
- [x] unit、contract、integration 和 eval 测试全部通过。
- [x] PostgreSQL 真实集成测试通过；使用隔离的 PostgreSQL 17 临时数据库。
- [x] Ruff、格式检查、mypy、Bandit 全部通过。
- [x] README、API 文档、架构文档、AGENTS.md 与实现一致。
- [x] 没有新增密钥、Token、日志、数据库、覆盖率或其他生成态文件。
- [x] 最终文档只声明“单租户多用户个人会话隔离”，不声明多租户或生产级 IAM。

验收记录（2026-09-19）：`TEST_POSTGRES_URL` 指向隔离 PostgreSQL 17 临时数据库时，
`.venv/bin/python -m pytest tests/ -q` 为 154 passed；`make lint`、`make type-check`、
`make security` 均通过；`make eval` 的离线固定集为 34/34。前端安全行为由静态契约测试、
认证/角色契约测试及后端跨用户集成测试共同覆盖。

### 8.6 回退要求

- 前端发布与后端 API 契约切换应协调进行；不能让旧前端继续发送可伪造 `user_id`。
- 回退前端时仍必须保留后端认证和所有权检查，不允许恢复匿名数据接口。
- 回退数据库 migration 前先停止新写入并备份；已生成的密码哈希和 Token 不导出为明文。

---

## 9. 分阶段验证命令

实际命令以实施时的 Makefile 和测试文件为准。建议最小验证矩阵如下。

### 第一阶段

```bash
.venv/bin/python -m pytest \
  tests/unit/test_passwords.py \
  tests/unit/test_tokens.py \
  tests/unit/test_auth_dependencies.py \
  tests/contract/test_auth_api.py -v

.venv/bin/python -m ruff check app/auth app/api/auth.py app/services/user_store.py tests
.venv/bin/python -m ruff format --check app tests
make type-check
make security
```

### 第二阶段

```bash
.venv/bin/python -m pytest \
  tests/unit/test_thread_store.py \
  tests/unit/test_working_memory_store.py \
  tests/integration/test_multi_agent_service.py \
  tests/integration/test_persistence_and_observability.py \
  tests/contract/test_agent_api.py \
  tests/contract/test_legacy_api_contracts.py -v

.venv/bin/python -m pytest -m postgres tests/integration/test_postgres_persistence.py -v
```

### 第三阶段

```bash
.venv/bin/python -m ruff check app tests
.venv/bin/python -m ruff format --check app tests
make type-check
make security
make test
make eval
```

`make check-all` 当前包含会修改源码的 `make format`，只读验收时不要直接使用。测试报告必须记录
实际执行命令、通过数量、跳过项和环境限制。

---

## 10. 迁移、发布与兼容策略

### 10.1 推荐发布顺序

1. 备份 PostgreSQL。
2. 部署第一阶段认证基础，但不对外宣称隔离完成。
3. 创建首个管理员和测试用户。
4. 执行第二阶段 migration，回填并隔离旧数据。
5. 部署第二阶段后端，验证 Bearer API 和双用户隔离。
6. 部署第三阶段前端。
7. 执行自动化与双用户手工验收。
8. 更新项目能力说明和交接文档。

### 10.2 兼容原则

- 保留 `/api/chat`、`/api/chat_stream`、`/api/aiops` 路由，但统一要求认证。
- 兼容路由中的旧 Session ID 只表示 Thread，不表示用户。
- 不保留允许客户端指定可信 `user_id` 的隐藏兼容入口。
- 调用方迁移完成且契约测试通过之前，不删除旧路径。
- 认证带来的 401 属于有意的安全契约变化，必须在 README 和 API 文档明确说明。

### 10.3 旧数据处理

- 共享环境先备份，再迁移。
- 所有缺少可信所有者的数据进入禁用 `legacy-system` 用户。
- 开发环境如决定清空演示数据，必须由用户明确授权，并使用可恢复或已备份方式执行。
- 迁移脚本不得从历史请求中的任意 `user_id` 推断真实账号。

---

## 11. 风险与应对

| 风险 | 后果 | 应对 |
|---|---|---|
| 只加登录，不改 Store | 已登录用户仍可越权 | 第二阶段强制 SQL 所有者过滤并做双用户测试 |
| 请求体仍有 `user_id` | 用户可伪造身份 | 删除字段，身份只来自 `CurrentUser` |
| 工作记忆先加载后校验 | 他人上下文进入 Agent | Store 查询时过滤，Context 构造前再次校验 |
| 旧数据错误归属 | 历史泄漏给真实用户 | 使用禁用 `legacy-system` 隔离 |
| JWT Secret 使用默认值 | Token 可被伪造 | Secret 必填、占位符文档、启动校验 |
| 禁用用户 Token 仍有效 | 离职账号继续访问 | 每次请求检查用户状态 |
| 前端长期保存 Token | XSS 后凭据暴露时间增加 | 第一版仅 sessionStorage，退出与 401 清理 |
| 同一浏览器缓存串用户 | B 看到 A 的本地历史 | 服务端历史为准，清理旧缓存或按 user 命名空间 |
| 管理员权限过大 | 内部隐私泄漏 | admin 默认不能读取他人聊天内容 |
| 新旧实例混合运行 | 新数据缺所有者或契约冲突 | migration 与后端切换同一发布窗口 |
| 为兼容增加无所有者回退 | 形成长期安全旁路 | 明确禁止按裸 thread_id/run_id 回退 |

---

## 12. 全计划完成定义

只有同时满足以下条件，整个计划才可标记为“已完成”：

- 三个阶段的验收条件全部通过并有可复现证据。
- 所有业务用户身份来自服务端认证结果。
- Thread、Run、Trace、Checkpoint 和工作记忆具有一致所有者。
- 任意两个普通用户之间无法读取、继续、删除或恢复对方会话。
- 普通用户 Metrics 只包含本人数据。
- 管理员权限边界与文档一致，默认不能查看他人聊天内容。
- 旧 Chat/AIOps 接口没有匿名或伪造用户旁路。
- 前端同一浏览器切换用户不会泄露上一用户的本地或服务端历史。
- Memory、SQLite、PostgreSQL 的所有权协议测试通过。
- PostgreSQL 空库升级、旧数据迁移和回退流程已验证。
- JSON/SSE 契约和单终态行为保持正确。
- 全量可行测试、静态检查和安全检查通过。
- README、架构文档、API 文档和 AGENTS.md 只描述已实现能力。
- 仓库没有新增凭据和生成态运行文件。

完成后的准确项目表述应为：

> 平台支持单租户下的本地多用户认证，并对个人会话、运行记录、Trace、Checkpoint 和跨轮工作
> 记忆执行用户级所有权隔离；企业知识库与只读工具仍由团队共享。

不得表述为：

- 已实现多租户 SaaS。
- 已接入企业 SSO。
- 已达到生产级 IAM 或合规审计要求。
- 管理员具备经过审计的聊天内容访问能力。
