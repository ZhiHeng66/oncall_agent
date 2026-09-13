# 跨轮工作记忆与压缩摘要：下一阶段实现交接

## 1. 任务目的

为企业知识服务多智能体平台增加**受控的跨轮工作记忆**。目标场景是 SaaS 企业内部技术支持：同一张工单或同一个排障会话通常需要多轮补充信息、查询动态数据、确认结论和生成处理建议。

本阶段要解决的问题不是“保存全部聊天记录”，而是：在下一轮请求到来时，系统能带入与当前案件相关、仍有效、可追溯的最小上下文，避免重复排查和上下文丢失。

目标效果：

```text
第 1 轮：客户无法登录，帮我排查。
第 2 轮：问题从上午 10 点开始，继续判断是否需要升级。
第 3 轮：告警恢复后，还需要继续按 P1 处理吗？
```

第 2、3 轮应能基于已确认事实、已查询 Evidence、未验证项和下一步计划继续工作；实时数据过期时必须重新查询，不能把旧数据当成当前事实。

## 2. 当前代码基线与事实边界

实施前必须先阅读以下模块：

- `app/agent/state.py`：顶层 `MultiAgentState`。
- `app/agent/graph.py`：顶层 Supervisor-Specialist 图、`_initial_state()` 和 Checkpoint Key。
- `app/services/multi_agent_service.py`：请求执行、SSE、最终运行记录写入。
- `app/services/persistence.py`：memory / sqlite / postgres 持久化资源生命周期。
- `app/services/run_store.py`：`agent_runs`、`agent_events` 的三套后端实现。
- `app/agent/runtime/checkpointer.py`：`AsyncPostgresSaver` 接入。
- `app/models/agent.py`：公开 API 契约。

### 2.1 已有能力

- 顶层图使用 `MultiAgentState` 保存单次运行中的路由、Agent 结果、Evidence、审核结果与最终答案。
- 每次 `/api/agent/run` 或 `/api/agent/run_stream` 都会生成新 `run_id`。
- LangGraph Checkpoint Key 当前为 `f"{thread_id}:{run_id}"`，见 `MultiAgentGraph._config()`。
- PostgreSQL 模式下，LangGraph `AsyncPostgresSaver` 保存单次图运行的多个状态快照；项目自建 `agent_runs` 和 `agent_events` 保存最终响应、指标和公开 Trace。
- `GET /api/agent/threads/{thread_id}` 能按业务 `thread_id` 查询历史请求与最终回答。

### 2.2 当前缺失能力

- 每一轮使用新的 `thread_id:run_id` Checkpoint Key，上一轮 State 不会自动注入下一轮。
- `agent_runs` 中的历史记录目前仅用于查询，不会自动进入 Supervisor、Knowledge、Data 或 Task Agent 的 Prompt/State。
- 没有案件摘要、事实提取、实时 Evidence 新鲜度判断或待办步骤延续。
- 当前没有认证、`tenant_id`、线程归属校验、RBAC 或租户级知识/数据隔离。客户端可传入 `user_id` 与 `thread_id`，它们不能作为生产安全边界。

### 2.3 明确不要做的事

- 不要把 LangGraph Checkpoint 直接当作跨轮记忆库。
- 不要把完整历史消息、完整工具原始返回或完整 Evidence 无上限拼入 Prompt。
- 不要将旧告警、日志、指标等动态数据无条件当作当前事实。
- 不要记录隐藏思维链、Prompt、密钥、Authorization、Cookie、未经限制的工具返回或个人敏感数据。
- 不要在未实现认证和租户权限前，宣称已实现多租户记忆隔离。
- 不要改变现有 `/api/chat`、`/api/chat_stream`、`/api/aiops` 兼容行为。

## 3. 推荐设计：案件上下文，而非无限聊天记忆

新增的核心概念建议命名为 `IncidentContext` 或 `WorkingMemoryContext`。它以业务 `thread_id` 为当前阶段的关联键；未来接入认证后，应以 `(tenant_id, user_id / authorized_case_id, thread_id)` 作为访问与隔离边界。

```text
原始运行记录（已有）
agent_runs / agent_events
    └─ 审计、历史展示、Trace 回放

案件工作记忆（新增）
working_memory_contexts
    └─ 下一轮推理使用的压缩摘要

实时证据（现有数据源）
Prometheus / 工单 / 日志 MCP
    └─ 按需重新查询，不作为永久“当前事实”
```

### 3.1 建议的 Pydantic 契约

新模型应放在 `app/agent/contracts.py` 或新建职责单一的 `app/agent/memory/contracts.py`，公共模型必须有完整类型标注和 `Field(default_factory=...)`。

推荐字段如下；名称可以微调，但语义不要省略：

```python
class MemoryEvidenceRef(BaseModel):
    source_type: Literal["knowledge", "data"]
    source_name: str
    summary: str
    observed_at: datetime | None
    expires_at: datetime | None
    run_id: str


class ConfirmedFact(BaseModel):
    statement: str
    evidence_refs: list[MemoryEvidenceRef] = Field(default_factory=list)
    recorded_at: datetime


class WorkingMemoryContext(BaseModel):
    thread_id: str
    ticket_id: str | None = None
    current_goal: str = ""
    service_name: str | None = None
    region: str | None = None
    incident_start: datetime | None = None
    incident_end: datetime | None = None
    confirmed_facts: list[ConfirmedFact] = Field(default_factory=list)
    unverified_items: list[str] = Field(default_factory=list)
    current_conclusion: str = ""
    next_steps: list[str] = Field(default_factory=list)
    summary: str = ""
    updated_at: datetime
    version: int
```

约束要求：

- `confirmed_facts` 必须有 Evidence 引用或明确标记为“用户声明，尚未验证”；不能让摘要模型凭空生成事实。
- 动态数据必须填写 `observed_at`，并按来源类型设置 `expires_at` 或新鲜度策略。
- `summary` 有明确上限，例如 1,500～2,000 字符；`confirmed_facts`、`unverified_items`、`next_steps` 都应有数量上限。
- 不保存完整文档正文、原始日志、原始 MCP 返回和隐藏推理。
- 摘要应保留事实与结论的差异：事实是证据支持的观察，结论是基于事实的暂时判断。

### 3.2 新鲜度策略

不要只按“历史越新越好”处理。建议按来源分别判断：

| 来源 | 建议策略 |
|---|---|
| 企业 SOP、服务等级、产品手册 | 可长期复用，但要保留文档版本/来源；知识库更新后可重新检索 |
| 工单状态 | 下一轮按需刷新；旧状态只能作为历史线索 |
| Prometheus 告警、CPU、内存、延迟、错误率 | 短 TTL；继续排障或询问“当前”时必须重新查询 |
| 日志查询结果 | 绑定服务、区域与时间窗口；不能自动外推到新的时间段 |
| 用户提供的描述 | 标注为“用户声明/待验证”，不可直接升级为确认事实 |

TTL 数值应集中配置在 `app/config.py`，不能散落在 Agent Prompt 或函数中。初期可只实现 `is_stale(now)` 规则，后续再做不同来源的精细 TTL。

## 4. 推荐执行链路

```text
请求进入 /api/agent/run 或 /run_stream
    ↓
鉴权与线程归属校验（当前未实现；先预留接口）
    ↓
WorkingMemoryStore.load(thread_id)
    ↓
过滤过期动态 Evidence，得到 compact_context
    ↓
将 compact_context 放入本轮初始 State / 结构化 AgentTask 上下文
    ↓
Supervisor / Task Agent 根据“当前问题 + 有效上下文”路由与规划
    ↓
执行本轮图；动态问题仍调用实时工具验证
    ↓
基于本轮 AgentResult、Evidence、错误和最终结果构造 MemoryUpdate
    ↓
规则校验 + 可选 LLM 结构化压缩
    ↓
WorkingMemoryStore.upsert(context)
    ↓
保存原有 agent_runs / agent_events，返回结果
```

### 4.1 状态注入建议

不要把任意历史文本塞入所有 Prompt。建议在顶层 `MultiAgentState` 中增加受限字段，例如：

```python
working_memory: WorkingMemoryContext | None
memory_context_summary: str
```

并在以下位置按职责使用：

- Supervisor：了解当前案件目标、已确认事实与未完成项，避免重复路由。
- Task Planner / Replanner：利用 `next_steps` 与未验证项；仍需根据当前问题和实时数据重新规划。
- Knowledge Agent：只在上下文明确需要制度/SOP 时获得压缩摘要，不传原始全量历史。
- Data Agent：使用服务、区域、时间窗口等受控参数提示；不得直接信任记忆中的旧实时结论。
- Reviewer：检查最终答案是否把过期 Evidence 说成“当前状态”。

注意：把 `working_memory` 放进顶层 State 会使其被单次运行 Checkpoint 保存。这是可以接受的前提是字段已压缩、清洗、限长；它不能取代新增的业务记忆表。

### 4.2 摘要更新建议

优先采用“规则提取 + LLM 受约束补充”的方案：

1. 从 `AgentResult.evidence`、`errors`、`ReviewResult`、Task 步骤结果中规则化提取可追溯材料。
2. 对已有 `WorkingMemoryContext` 与本轮材料执行去重、过期清理、数量截断。
3. 若需要压缩，再调用 LLM 的 `with_structured_output(WorkingMemoryUpdate)`。
4. LLM 输入只能包含旧摘要、受限 Evidence 摘要、本轮错误和最终结论；输出必须经过 Pydantic 校验。
5. 对新增 `confirmed_facts` 做引用校验：没有证据引用的事实降级为 `unverified_items`，或直接拒绝写入。

不要让 LLM 从所有 `agent_runs` 历史中自由总结，也不要在每轮都无条件调用摘要模型。建议只有以下情况触发压缩：

- 摘要超过字符/事实数量阈值；
- 新运行产生了新的有效 Evidence 或新结论；
- 用户明确请求“总结当前排查进展”；
- 案件状态从排查中变为已解决/部分解决。

## 5. 存储与迁移建议

### 5.1 后端抽象

当前 `PersistenceFactory` 同时支持 memory、sqlite、postgres。为保证测试和本地开发可用，应增加独立协议，而不是将 PostgreSQL SQL 写进 Agent：

```python
class WorkingMemoryStore(Protocol):
    async def get(self, thread_id: str) -> WorkingMemoryContext | None: ...
    async def upsert(self, context: WorkingMemoryContext) -> None: ...
    async def delete_thread(self, thread_id: str) -> None: ...
```

建议实现：

- `MemoryWorkingMemoryStore`：单元测试和 memory 模式。
- `SqliteWorkingMemoryStore`：保持默认开发环境一致。
- `PostgresWorkingMemoryStore`：共享部署模式。

将其加入 `PersistenceResources`，由 `MultiAgentService.startup()` 获取。不要在 import 阶段连接数据库。

### 5.2 PostgreSQL 表建议

表名可使用 `agent_working_memories`：

```text
agent_working_memories
├─ thread_id             TEXT PRIMARY KEY
├─ context_json          JSONB NOT NULL
├─ version               INTEGER NOT NULL
├─ created_at            TIMESTAMPTZ NOT NULL
└─ updated_at            TIMESTAMPTZ NOT NULL
```

可选扩展字段：

```text
tenant_id
owner_user_id
ticket_id
status
```

当前没有可信认证，因此不要假装这些字段已经形成安全隔离；但可以为后续迁移预留 nullable 列和索引设计。接入认证后，应将访问 API 改为按 `(tenant_id, authorized_user_or_case, thread_id)` 查询，不能只按裸 `thread_id` 查询。

新增表必须通过 Alembic 迁移管理；不要在应用启动时 `CREATE TABLE`。SQLite 对应表要在 Store 的 `setup()` 中创建，与现有 `agent_runs` 方式一致。

### 5.3 删除语义

当前 `clear_thread()` 会删除 `agent_runs`、`agent_events`，并循环删除每个 `thread_id:run_id` 的 LangGraph Checkpoint。实现跨轮记忆后，也必须在同一清理流程中删除对应 `WorkingMemoryContext`。

不要求 Checkpoint、RunStore 和 MemoryStore 跨三套逻辑形成分布式事务；但删除失败必须记录结构化错误，不得静默留下“用户以为已删除、实际仍保留”的记忆。

## 6. 分阶段实施建议

### 阶段 A：契约、存储与可测试基础

1. 新增 Pydantic 工作记忆模型、长度限制和新鲜度规则。
2. 新增 `WorkingMemoryStore` Protocol，以及 memory/sqlite/postgres 实现。
3. 新增 Alembic migration，创建 PostgreSQL 表、索引和必要约束。
4. 将 Store 纳入 `PersistenceFactory`、`PersistenceResources` 和 `MultiAgentService` 生命周期。
5. 扩展 `clear_thread()`，同时清理工作记忆。

验收：不改变现有 API 行为；三种持久化后端均能 load/upsert/delete；无外部 LLM、Milvus、MCP 依赖的单测通过。

### 阶段 B：加载、状态注入与规则更新

1. 在每次 run/stream 开始前加载工作记忆。
2. 过滤失效实时 Evidence，生成严格限长的 `memory_context_summary`。
3. 将摘要注入顶层初始 State，并按最小权限传递给 Supervisor / Task Agent。
4. 在图运行结束后，根据结构化 `AgentResult`、Evidence、错误、Review 结果更新记忆。
5. 对 `run()` 和 `stream()` 复用同一更新逻辑，避免两种接口行为不一致。

验收：第二轮请求能得到第一轮的有效案件摘要；过期实时 Evidence 不会被标记为“当前”；没有可更新材料时不创建空记忆。

### 阶段 C：受约束 LLM 压缩与质量控制

1. 定义 `WorkingMemoryUpdate` Pydantic 输出契约。
2. 使用共享 LLM Factory 与 `with_structured_output` 实现摘要压缩，禁止在节点内创建独立模型。
3. 增加事实证据引用校验、去重、截断和摘要大小预算。
4. 在 Reviewer 中增加“是否引用过期 Evidence / 是否把未验证项说成事实”的检查。
5. 记录非敏感的 `memory_loaded`、`memory_updated`、`memory_pruned` SSE/Trace 事件。

验收：模型输出不合规时保留上一个合法摘要或使用规则化降级摘要；不得因摘要失败导致主回答失败。

### 阶段 D：认证与多租户前置能力（后续，不作为本阶段完成标准）

1. 从认证 Token 派生 `user_id`、`tenant_id`，不再信任客户端请求体。
2. 为线程、案件、知识库、MCP 调用和记忆读取增加统一授权范围。
3. 增加跨用户、跨租户访问拒绝测试。

在阶段 D 完成前，文档只能称为“按 `thread_id` 的开发/单组织工作记忆”，不得称为多租户记忆。

## 7. 必须覆盖的测试

### 单元测试

- 空记忆、有效记忆、过期记忆的加载和裁剪。
- 结构化模型的长度、数量、时间戳、Evidence 引用校验。
- 相同事实去重，旧结论被新 Evidence 覆盖时的规则。
- 用户声明不能未经证据直接转为确认事实。
- 摘要模型失败、超时、返回非法结构时的降级。
- 工作记忆 Store 的 upsert 幂等、delete 和线程隔离。
- `clear_thread()` 同时删除运行记录、Checkpoint 和工作记忆的行为。

### 集成测试

- 两次使用同一 `thread_id` 的请求：第二次确实收到第一轮有效摘要。
- 两次不同 `thread_id` 的请求：绝不交叉读取记忆。
- 数据工具 Evidence 已过期：第二轮计划应要求重新查询，而非沿用为当前事实。
- MCP 查询失败：记忆记录“未验证/数据源不可用”，不得写成“无告警/无日志”。
- `run()` 与 `run_stream()` 更新后的记忆等价。
- PostgreSQL 模式下：迁移、重启后读取、删除和多实例共享测试。

普通测试必须使用 Fake LLM、Fake Tool、Fake Clock 或依赖注入；不得依赖真实 DashScope、Milvus 或在线 MCP Server。

## 8. 质量与安全检查清单

- [ ] 不暴露 Prompt、隐藏推理、密钥、Authorization、Cookie、完整异常栈和原始 MCP 返回。
- [ ] 每条确认事实都有 Evidence 引用或清晰的“用户声明、未验证”标记。
- [ ] 动态 Evidence 有观察时间和过期判断。
- [ ] 摘要、事实、待办和 Evidence 都有长度/数量上限。
- [ ] 没有将 `thread_id` 当作认证或租户权限证明。
- [ ] Memory、SQLite、PostgreSQL 三个后端均有测试覆盖。
- [ ] 新迁移可重复执行，应用启动只校验 Schema，不隐式变更生产数据库。
- [ ] `make test-quick`、相关单测、集成测试、`make lint` 和可行的类型检查通过。
- [ ] README 或架构文档标明：当前是跨轮案件摘要，不是无限长对话记忆，也不是多租户记忆。

## 9. 建议的面试表述（仅在实现与测试完成后使用）

> 我们没有把完整聊天记录无上限拼进 Prompt，而是以 thread 或工单为边界维护结构化案件上下文：保存当前目标、已确认事实及其 Evidence、待验证项、下一步计划和压缩结论。每轮开始前会加载并裁剪摘要；监控、日志和工单等动态证据按时间判断是否过期，涉及“当前状态”时仍重新查询。LangGraph Checkpoint 用于单次工作流的状态快照和恢复，业务工作记忆则由独立的 PostgreSQL 表管理。这样既减少重复排查，也避免旧实时数据、无限上下文和未经授权的跨会话读取带来的风险。
