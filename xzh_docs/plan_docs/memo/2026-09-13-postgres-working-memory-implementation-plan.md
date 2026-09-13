# PostgreSQL 跨轮工作记忆与上下文压缩实施计划

> 项目：企业知识服务多智能体协同平台
>
> 文档日期：2026-09-13
>
> 文档状态：实施中；阶段一已于 2026-09-13 完成并通过验收，阶段二至四计划中
>
> 适用分支：`multi-agents`
>
> 实施范围：统一 Multi-Agent 主链路及其 `/api/chat` 兼容适配
>
> 目标后端：PostgreSQL
>
> 非目标：长期用户画像、跨租户记忆、向量化长期记忆、无限聊天历史、旧 `/api/aiops` 记忆改造

## 1. 实施结论

本阶段采用“结构化案件记忆 + 最近对话尾部”的混合上下文方案：

- PostgreSQL 中新增独立的结构化工作记忆快照，按业务 `thread_id` 保存当前案件目标、
  关键实体、用户声明、证据观察、暂时结论、待验证项和下一步。
- 最近少量用户可见对话不重复写入记忆快照，而是从现有 `agent_runs` 中按
  `thread_id` 有界读取，用于解决“继续”“刚才”“它”等语言指代。
- 每轮开始时将结构化记忆和最近对话组合成严格限长、按 Agent 职责过滤的 Context Pack。
- 每轮结束后只生成结构化 `WorkingMemoryUpdate`，再由确定性 Reducer 完成校验、归并、
  去重、失效和裁剪；LLM 不直接覆盖完整记忆。
- 上下文压缩采用“每轮确定性裁剪 + 达到阈值时才调用 LLM”的两层机制。
- LangGraph Checkpoint 继续用于单次运行状态快照，工作记忆由项目自建 PostgreSQL 表管理，
  两者不得混用。

本次只实现 PostgreSQL 工作记忆后端。普通单元测试使用 Fake Store，不要求真实 PostgreSQL；
SQLite 和 memory 部署模式继续运行现有能力，但明确不启用跨轮工作记忆，不伪装为已支持。

---

## 2. 当前源码基线

实施时必须以提交代码为事实来源，重点基线如下。

### 2.1 当前已有能力

- `app/agent/state.py` 中的 `MultiAgentState` 已区分 `user_id`、`thread_id` 和 `run_id`，
  并保存本轮路由、Agent 结果、Evidence、Reviewer 结果和最终答案。
- `app/agent/graph.py` 中顶层图的 Checkpoint Key 为 `thread_id:run_id`，同一业务线程的不同
  run 不会自然合并状态。
- `app/services/multi_agent_service.py` 为每次 JSON 或 SSE 请求创建新 `run_id`，并在运行完成后
  保存公开响应、事件和指标。
- `app/services/run_store.py` 中的 PostgreSQL RunStore 已保存 `agent_runs` 和 `agent_events`，
  并能按 `thread_id` 返回公开历史。
- `app/services/persistence.py` 已通过 FastAPI lifespan 管理 PostgreSQL 连接池、
  `AsyncPostgresSaver` 和 `PostgresRunStore`。
- `AgentTask.context` 已被 Knowledge、Data 和 Task Planner 使用，可作为 Specialist 接收受控
  上下文的现有契约入口。
- `/api/chat` 和 `/api/chat_stream` 已适配到 `MultiAgentService`，与统一 Agent 主链路共享
  `thread_id` 语义。

### 2.2 当前缺失能力

- `MultiAgentState` 没有跨轮记忆或最近对话字段。
- Supervisor 只接收当前用户请求、本轮 Agent 结果和 Reviewer 反馈，不接收历史案件上下文。
- `agent_runs` 中的历史仅用于查询，不会进入下一轮路由和规划。
- 当前 `Evidence` 没有统一的 `observed_at`、时间范围、有效期和查询范围语义。
- Data Agent 生成 Evidence 时只记录工具、Server 和类别，无法可靠判断旧动态数据能否复用。
- Reviewer 无法判断候选答案是否把过期观察或用户声明描述成当前事实。
- JSON 和 SSE 各自包含运行完成与持久化流程，直接分别接入记忆会产生行为漂移风险。
- 当前没有认证、`tenant_id`、线程归属校验和 RBAC，客户端传入的 `user_id/thread_id`
  不能作为生产安全边界。

### 2.3 需要同时规避的现有风险

Task Agent 子图当前按 `thread_id:task_id` 构造内部 Checkpoint Key，而 `task_id` 可能在不同
run 中重复。实施跨轮记忆时必须避免把这种 Checkpoint 命中误当作工作记忆；主链路向 Task
Agent 委派时应带入 `run_id`，将内部执行键调整为至少包含：

```text
thread_id:run_id:task_id
```

这项修改只用于保持不同 run 的 Task 子图状态隔离，不代表 Task Agent 获得长期记忆。

---

## 3. 目标、非目标与事实边界

### 3.1 目标

1. 同一 `thread_id` 的后续请求能够在应用重启后恢复案件工作记忆。
2. 保留最近少量用户可见对话，支持自然的连续追问和指代解析。
3. 区分用户声明、证据支持的观察、暂时结论、决策和待办。
4. 对告警、日志、指标和工单状态等动态 Evidence 执行可测试的新鲜度判断。
5. 为不同 Agent 构造最小必要上下文，不复制完整历史和原始工具输出。
6. 对记忆大小、条目数、压缩调用、并发重试和数据库操作设置硬限制。
7. 保持 `/api/agent/run`、`/api/agent/run_stream`、`/api/chat` 和
   `/api/chat_stream` 的现有公共响应兼容。
8. 保持每条 SSE 流只有一个终态事件。
9. 同一线程发生并发请求时，不允许无提示的最后写入覆盖。
10. 清理线程时同步删除运行记录、事件、顶层 Checkpoint 和工作记忆。

### 3.2 非目标

本计划不实施：

- 按语义相似度检索长期用户记忆。
- 用户画像、偏好推断和跨案件个性化。
- 自动把不同 `thread_id` 合并为同一个案件。
- 多租户安全隔离或基于客户端 `user_id` 的可信授权。
- 保存完整对话、完整文档、完整日志或原始 MCP 响应。
- 保存隐藏思维链、Prompt、API Key、Authorization、Cookie 或异常栈。
- 将 LangGraph Checkpoint 描述为业务工作记忆。
- 第一阶段为 memory、SQLite 实现正式工作记忆后端。
- 第一阶段改造仍直接调用 Task Agent 的旧 `/api/aiops` 链路。
- 声称长期记忆、持久化用户画像、生产级多租户隔离或量化效果提升。

### 3.3 跨重启连续性的前提

跨重启恢复依赖稳定的业务 `thread_id`：

- 服务进程重启但 `thread_id` 不变：可以从 PostgreSQL 恢复。
- 浏览器刷新但客户端继续携带原 `thread_id`：可以恢复。
- 客户端创建了新的 `thread_id`：默认视为新案件，不自动读取旧线程。
- 未来如需通过工单恢复，应引入可信 `ticket_id/authorized_case_id`，不能使用模糊文本匹配
  自动合并线程。

---

## 4. 目标数据模型

### 4.1 工作记忆快照

建议在 `app/agent/memory/contracts.py` 中定义 Pydantic 模型。公共可变字段必须使用
`Field(default_factory=...)`。

```python
class WorkingMemoryContext(BaseModel):
    schema_version: int = 1
    thread_id: str
    owner_user_id: str
    version: int
    current_goal: str = ""
    entities: dict[str, str] = Field(default_factory=dict)
    items: list[MemoryItem] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    compressed_summary: str = ""
    last_run_id: str | None = None
    created_at: datetime
    updated_at: datetime
```

`owner_user_id` 第一阶段仅用于发现意外的 user/thread 混用和后续迁移预留。由于请求中的
`user_id` 仍由客户端提供，它不能形成真实安全隔离，也不得据此宣称已实现线程授权。

### 4.2 记忆条目

```python
MemoryItemKind = Literal[
    "user_claim",
    "evidence_observation",
    "conclusion",
    "decision",
]
MemoryItemStatus = Literal["active", "superseded", "resolved"]
VerificationStatus = Literal["unverified", "supported"]
TemporalScope = Literal["static", "snapshot", "time_window"]


class MemoryItem(BaseModel):
    item_id: str
    kind: MemoryItemKind
    statement: str
    status: MemoryItemStatus = "active"
    verification: VerificationStatus
    evidence_refs: list[MemoryEvidenceRef] = Field(default_factory=list)
    temporal_scope: TemporalScope
    observed_at: datetime | None = None
    valid_until: datetime | None = None
    query_scope: str | None = None
    created_run_id: str
    updated_run_id: str
    created_at: datetime
    updated_at: datetime
```

必须保持以下不变量：

- `supported` 条目至少有一个可解析的 Evidence 引用。
- 没有 Evidence 的用户陈述只能保存为 `user_claim/unverified`。
- `snapshot` 和 `time_window` 条目必须有服务端生成的观察时间。
- `conclusion` 是可被后续证据替代的暂时判断，不得冒充事实。
- 新证据推翻旧条目时将旧条目标记为 `superseded`，不得静默覆盖。
- 工具失败只可形成错误或未验证项，不得写成“没有告警”“没有日志”等成功观察。

### 4.3 Evidence 引用

工作记忆只保存可追踪且有界的引用，不复制完整 Evidence 内容：

```python
class MemoryEvidenceRef(BaseModel):
    run_id: str
    agent_name: str
    source_type: EvidenceSourceType
    source_name: str
    source_id: str | None = None
    excerpt: str
    observed_at: datetime | None = None
```

`excerpt` 必须经过现有安全清洗并限制长度。完整公开来源仍由 `agent_runs.response_json` 和
Trace 提供；原始 MCP 返回不进入工作记忆。

### 4.4 Evidence 时间语义

现有 `Evidence` 契约建议增加：

```python
observed_at: datetime | None
temporal_scope: Literal["static", "snapshot", "time_window"]
valid_until: datetime | None
query_scope: str | None
```

语义要求：

| 来源 | 时间语义 | 使用方式 |
|---|---|---|
| 企业手册、SOP、制度 | `static` | 可复用，但保留文档来源和版本线索 |
| 当前告警、工单状态 | `snapshot` | 工具完成时记录观察时间，过期后必须重新查询 |
| 日志、指标 | `time_window` | 绑定服务、区域和查询时间窗，不外推到其他范围 |
| 用户描述 | 不属于 Evidence | 保存为 `user_claim/unverified` |

`observed_at`、`valid_until` 和 `query_scope` 由代码和配置策略决定，不允许压缩模型自由生成。
TTL 按工具或来源类别集中配置在 `app/config.py`，不得分散在 Prompt 中。

### 4.5 记忆更新契约

压缩器只提出增量修改：

```python
class WorkingMemoryUpdate(BaseModel):
    goal_update: str | None = None
    entity_updates: dict[str, str] = Field(default_factory=dict)
    additions: list[MemoryItemProposal] = Field(default_factory=list)
    resolve_item_ids: list[str] = Field(default_factory=list)
    supersede_item_ids: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    summary_candidate: str | None = None
```

模型不能修改 `thread_id`、版本、服务端时间戳和来源 run，也不能删除任意未知 item ID。

---

## 5. PostgreSQL Schema 与存储边界

### 5.1 工作记忆表

新增 Alembic migration，项目拥有的表建议为：

```text
agent_working_memories
├── thread_id             TEXT PRIMARY KEY
├── owner_user_id         TEXT NOT NULL
├── schema_version        INTEGER NOT NULL
├── version               BIGINT NOT NULL
├── context_json          JSONB NOT NULL
├── last_run_id           TEXT NULL
├── created_at            TIMESTAMPTZ NOT NULL
└── updated_at            TIMESTAMPTZ NOT NULL
```

建议约束：

- `version >= 1`。
- `schema_version >= 1`。
- `jsonb_typeof(context_json) = 'object'`。
- `updated_at >= created_at`。

第一阶段不创建 JSONB GIN 索引，因为运行时只按主键 `thread_id` 加载整个快照。避免为尚未
存在的 JSON 内查询需求增加写放大和维护成本。

`last_run_id` 第一阶段不强制建立外键。清理线程时由应用按明确顺序删除记忆、运行记录和
Checkpoint；未来若增加不可变记忆修订表，再重新评估外键和级联策略。

### 5.2 Store 协议

正式实现只提供 `PostgresWorkingMemoryStore`，单元测试使用 `FakeWorkingMemoryStore`：

```python
class WorkingMemoryStore(Protocol):
    async def load(self, thread_id: str) -> WorkingMemoryContext | None: ...
    async def create(self, context: WorkingMemoryContext) -> bool: ...
    async def compare_and_swap(
        self,
        context: WorkingMemoryContext,
        expected_version: int,
    ) -> bool: ...
    async def delete_thread(self, thread_id: str) -> None: ...
```

不得使用无条件 `upsert(context)`。同一线程的两个并发 run 如果都从 version 3 开始，只有
一个请求可以成功写入 version 4；另一个请求必须重新加载、重新合并并执行一次有界重试。

### 5.3 生命周期

`PersistenceResources` 在 PostgreSQL 模式下增加 `working_memory_store`，复用现有
lifespan 管理的异步连接池：

```text
FastAPI lifespan
    |
    `-- PersistenceFactory.open(postgres)
           |-- AsyncConnectionPool
           |-- AsyncPostgresSaver
           |-- PostgresRunStore
           `-- PostgresWorkingMemoryStore
```

要求：

- 不在 import 阶段连接 PostgreSQL。
- 不为每次请求创建连接池。
- PostgreSQL 模式启动时检查工作记忆表是否存在。
- Schema 缺失时明确启动失败，并提示执行迁移；不得在启动时隐式修改生产 Schema。
- 日志不得输出完整 DSN 或记忆正文。
- 非 PostgreSQL 模式使用显式禁用状态，不静默创建 SQLite 工作记忆表。

### 5.4 运行完成的一致性

`agent_runs`、`agent_events` 和 `agent_working_memories` 都是项目业务表。最终目标是在同一个
PostgreSQL 事务中完成：

```text
BEGIN
    写入 agent_runs
    写入 agent_events
    创建或按 expected_version 更新 agent_working_memories
COMMIT
```

LangGraph Checkpoint 不加入该事务，它是图执行快照而不是业务提交记录。

如果记忆增量生成或校验失败，应提交 run 和 event，但不修改旧记忆，并记录经过清洗的
非致命记忆错误。不能因为辅助压缩失败把已经有效的主回答改写为失败。

如果事务中的 CAS 发生版本冲突：

1. 回滚本次完成事务。
2. 重新加载最新记忆。
3. 将本轮 `WorkingMemoryUpdate` 重新应用到最新版本。
4. 最多再提交一次。
5. 仍冲突时保存 run/event，不覆盖记忆，并记录 `memory_update_conflict`。

为避免 Service 层直接操作 SQL，实施时应为 PostgreSQL 完成流程提供职责清晰的持久化提交
接口或 Unit of Work；不要把数据库事务逻辑写进 Agent 节点。

---

## 6. 跨轮加载与 Context Pack

### 6.1 每轮加载顺序

统一 JSON 和 SSE 请求在进入图前执行：

1. 生成本轮 `run_id`。
2. 按 `thread_id` 加载 `WorkingMemoryContext`。
3. 从 `agent_runs` 有界加载最近若干轮用户可见消息。
4. 使用 Pydantic 按 `schema_version` 校验数据库 JSONB。
5. 根据受控时钟和新鲜度策略区分有效、过期和范围不匹配的条目。
6. 按当前请求和目标 Agent 构造限长 Context Pack。
7. 将结构化快照和使用摘要放入本轮初始 `MultiAgentState`。
8. 使用新的 `thread_id:run_id` 启动顶层图。

数据库中不存在记忆时，使用显式空上下文，不创建空记录。只有本轮产生值得延续的目标、
声明、Evidence、结论、未完成项或下一步时才创建工作记忆。

### 6.2 最近对话尾部

RunStore 增加有界读取能力，而不是调用现有 `get_thread()` 加载完整历史后再裁剪：

```python
async def get_recent_thread(
    thread_id: str,
    limit: int,
) -> list[PublicConversationTurn]: ...
```

PostgreSQL 查询按 `created_at DESC, run_id DESC` 限制数量，返回后恢复为时间正序。需要同时
限制：

- 最大轮数。
- 单条用户消息长度。
- 单条助手回答长度。
- 最近对话整体长度。

最近对话只包含用户可见请求和最终公开回答，不包含 Specialist 过程消息、Prompt、原始工具
返回或异常栈。

### 6.3 顶层状态

建议增加：

```python
working_memory: WorkingMemoryContext | None
recent_turns: list[PublicConversationTurn]
memory_usage: MemoryUsage
```

`memory_usage` 只记录有业务价值的摘要，例如：

- 是否成功加载。
- 加载的版本。
- 本轮选择了哪些 item ID。
- 哪些动态条目已过期。
- 是否因预算执行裁剪。

不要在状态中同时保存一份独立、可被随意修改的完整 Prompt 字符串作为第二事实源。
角色化文本由统一 Renderer 根据结构化状态生成。

### 6.4 Agent 级上下文最小化

| 使用方 | 应接收 | 不应接收 |
|---|---|---|
| Supervisor | 当前目标、关键实体、有效事实、过期提示、待办、最近对话 | 原始文档、原始工具结果 |
| Knowledge | 目标、产品/服务实体、需查证的制度问题、文档版本线索 | 旧监控和完整对话 |
| Data | 服务、区域、时间窗、需刷新项、旧查询范围 | 把旧实时结果描述成当前事实 |
| Task | 目标、有效事实、失败项、未完成步骤、下一步 | 全量历史和无关 Specialist 过程 |
| Reviewer | 当前请求、候选答案、本轮 Evidence、错误、所用记忆的新鲜度 | 新工具权限和新证据生成能力 |

Supervisor 返回 `RouteDecision` 后，由确定性代码根据目标 Agent 生成或补充
`AgentTask.context`。不能依赖 Supervisor LLM 自觉复制所有必要记忆，也不能让它自由选择
任意历史内容。

### 6.5 上下文预算与裁剪顺序

Context Pack 超过预算时按以下优先级保留：

1. 当前用户请求。
2. 当前目标和关键实体。
3. 与当前请求相关且仍有效的 Evidence 观察。
4. 未验证项、阻塞和下一步。
5. 最近一轮用户可见对话。
6. 第二轮最近对话。
7. 过期观察的简短历史提示。
8. 已解决和已被替代的条目优先移除。

所有预算都从配置读取。首期使用可测试的字符预算即可，不需要先实现复杂 Token 计费；
不得在没有数据集的情况下宣称某个预算具有最优效果。

### 6.6 记忆依赖型快速路径

当前 direct 和 single 成功路径可能跳过 Reviewer。加入记忆后，如果回答使用了历史工作记忆，
则必须满足以下一种策略：

- 禁止记忆依赖型请求走 direct；或
- `memory_usage.used=True` 时强制进入 Reviewer。

本计划推荐第二种，使普通无状态闲聊继续保持快速路径，同时允许 Reviewer 检查旧 Evidence、
用户声明和结论边界。

---

## 7. 记忆更新与压缩策略

### 7.1 更新输入

每轮完成后，Memory Updater 只能读取：

- 上一版合法 `WorkingMemoryContext`。
- 当前用户请求。
- 本轮结构化 `AgentResult`。
- 受限 Evidence 摘要和引用 ID。
- Task artifacts 中可公开的步骤状态。
- 本轮错误和 Reviewer 结果。
- 已清洗的最终回答。

不得读取或保存隐藏思维链、完整 Provider 响应、完整 MCP 结果和完整文档正文。

### 7.2 确定性更新

每轮都执行的 Reducer 负责：

- 校验 `WorkingMemoryUpdate` 中的 item ID 和 Evidence 引用。
- 将没有 Evidence 的用户陈述降级为 `user_claim/unverified`。
- 为动态 Evidence 使用服务端观察时间和 TTL。
- 按稳定 ID、来源和规范化文本去重。
- 根据新 Evidence 将旧结论标记为 `superseded`。
- 将完成的待办标记为 resolved 或从活跃列表中移除。
- 限制实体、条目、待验证项、下一步和 Evidence 引用数量。
- 移除 Prompt Context 中不再需要的 resolved/superseded 明细。
- 产生新的版本，但不自行访问数据库。

Reducer 必须是无网络依赖、可注入时钟、可重复测试的纯逻辑组件。

### 7.3 LLM 压缩

LLM 压缩仅在以下条件之一满足时触发：

- 活跃条目或摘要超过配置阈值。
- 新增了需要语义归并的多条 Evidence。
- 当前目标或结论发生重要变化。
- 案件进入已解决或部分解决状态。
- 用户明确要求总结当前进展。

压缩器通过共享 `app/core/llm_factory.py` 创建受控模型，使用
`with_structured_output(WorkingMemoryUpdate)`，不得在节点内单独硬编码模型配置。

压缩结果仍必须经过 Reducer 校验。模型无权：

- 把未验证声明升级为支持事实。
- 创建不存在的 Evidence 引用。
- 把失败工具调用改写成成功观察。
- 删除未在输入中出现的条目。
- 修改版本、线程、run 或服务端时间。
- 把过期 Evidence 重新标记为当前有效。

模型超时、异常或输出非法时，保留旧的合法摘要和确定性更新，不影响主回答终态。

### 7.4 不应更新记忆的情况

- 普通问候或无案件信息的闲聊。
- 图在获得终态前被客户端取消。
- 只有隐藏推理或模型草稿，没有结构化业务结果。
- 工具调用失败且没有形成新的待验证项或阻塞信息。
- 输出与已有记忆完全相同。

---

## 8. JSON、SSE 与失败语义

### 8.1 统一完成流程

`MultiAgentService.run()` 与 `stream()` 应复用同一个内部完成流程：

```text
图得到终态
    -> 标准化 AgentRunResponse
    -> 构造并校验 WorkingMemoryUpdate
    -> PostgreSQL 提交 run/event/memory
    -> JSON 返回或 SSE 发出唯一终态
```

流式接口只有获得 `graph_completed` 后才更新工作记忆。客户端断开导致生成器取消时，不把
半完成步骤写入工作记忆。

### 8.2 失败分类

| 失败 | 主回答 | 工作记忆 | 可观察行为 |
|---|---|---|---|
| 记忆不存在 | 正常运行 | 视为新线程 | 记录未命中，不报错 |
| 记忆 JSON 非法/版本不支持 | 无历史运行或按依赖程度部分成功 | 不覆盖坏数据 | 清洗日志，标记加载失败 |
| 记忆压缩失败 | 正常返回本轮有效回答 | 保留旧摘要/确定性更新 | 记录非致命失败 |
| CAS 首次冲突 | 暂不影响 | 重载并重试一次 | 记录冲突次数 |
| CAS 再次冲突 | 正常保存本轮 run | 不覆盖新版本记忆 | 记录 `memory_update_conflict` |
| PostgreSQL 整体不可用 | 沿用现有持久化失败策略 | 无法更新 | 不静默降级到本地存储 |
| 客户端断开 | 取消当前流 | 不更新 | 不生成伪终态记忆 |

第一阶段不要求向公共响应新增记忆正文或内部错误字段。日志和 Trace 只记录版本、条目数、
裁剪数、耗时和状态，不记录实际记忆内容。若后续新增公开 `memory_loaded`、
`memory_updated` 等 SSE 类型，必须在同一修改中更新 Pydantic 事件枚举、契约测试、前端和
API 文档。

---

## 9. 分阶段实施与验收条件

整个实施划分为四个阶段。每个阶段必须独立通过对应验收，才能进入下一阶段。

### 阶段一：领域契约、时间语义与 PostgreSQL Store

> 实施状态：已完成。实际代码、迁移和验证结果见
> `xzh_docs/handoff_docs/2026-09-13-postgres-working-memory-phase1-handoff.md`。

#### 实施内容

1. 新建 `app/agent/memory/`，定义工作记忆、记忆条目、Evidence 引用和更新契约。
2. 扩展 Evidence 的观察时间、时间范围、有效期和查询范围语义。
3. 在 Data Agent 的工具成功边界记录服务端观察时间；动态 TTL 由集中配置计算。
4. 增加工作记忆大小、条目数、最近轮数、TTL 和并发重试配置。
5. 新增 Alembic migration 创建 `agent_working_memories`。
6. 实现 `PostgresWorkingMemoryStore` 的 load、create、CAS 和 delete。
7. 将 Store 接入 PostgreSQL `PersistenceResources` 和 lifespan。
8. PostgreSQL 模式启动时验证 Schema；非 PostgreSQL 模式显式禁用记忆。
9. 提供测试用 Fake Store，不实现正式 Memory/SQLite Store。

#### 测试要求

- Pydantic 模型默认值、长度、数量和枚举校验。
- `supported` 条目缺少 Evidence 引用时拒绝。
- snapshot/time-window 缺少观察时间时拒绝。
- 用户声明不会自动成为支持事实。
- PostgreSQL migration upgrade 可执行且 Schema 约束正确。
- PostgreSQL Store create/load/CAS/delete。
- 相同 expected version 只有一次更新成功。
- 两个不同 `thread_id` 绝不交叉读取。
- 应用重启后能重新加载同一快照。
- Schema 缺失时启动明确失败。

#### 验收条件

- PostgreSQL 可以持久保存并跨进程加载结构化工作记忆。
- 并发写入不会发生无提示覆盖。
- 所有时间字段均由代码或受控时钟生成，不依赖模型猜测。
- 非 PostgreSQL 模式不创建工作记忆表，也不影响现有测试和启动行为。
- 尚未把记忆注入图，因此本阶段不得宣称已实现跨轮回答连续性。

### 阶段二：跨重启加载、最近对话与角色化上下文

#### 实施内容

1. 为 RunStore 增加有界 `get_recent_thread()` 查询。
2. 实现 Context Builder、Freshness Policy 和角色化 Renderer。
3. 在每轮 JSON/SSE 调图前加载工作记忆和最近对话。
4. 扩展 `MultiAgentState`，保存结构化记忆、最近对话和 `memory_usage`。
5. 扩展 Supervisor 输入，使其能理解当前目标、未完成项和过期提示。
6. Supervisor 路由后由确定性代码为 Knowledge、Data、Task 构造最小
   `AgentTask.context`。
7. 记忆依赖型回答强制经过 Reviewer。
8. Reviewer 检查过期观察和未验证声明是否被当成事实。
9. Task 子图执行键加入 `run_id`，保持跨 run 隔离。

#### 测试要求

- 最近对话 SQL 只返回配置数量，并按正序组装。
- Context Pack 在字符预算内，裁剪顺序符合设计。
- Supervisor 能在“继续排查”请求中读取上一轮目标。
- Knowledge 不接收旧动态工具正文。
- Data 收到服务、区域、时间窗口和需要重查的过期项。
- Task 收到未完成步骤和有效事实。
- 不同线程的 Context Pack 完全隔离。
- 相同线程不同 run 的 Task Checkpoint 不串状态。
- 记忆依赖型 direct 回答不会绕过 Reviewer。
- JSON 和 SSE 都能加载相同上下文，但尚不要求更新记忆。

#### 验收条件

- PostgreSQL 中预置的记忆在服务重启后能够进入下一轮 Supervisor 和 Specialist。
- 同一 `thread_id` 的“继续、刚才、它”等追问可以结合最近对话解释。
- 过期动态 Evidence 只能作为历史线索，不能被描述为当前状态。
- Context Pack 严格限长，且不包含原始 MCP 返回、完整文档或隐藏推理。
- 现有统一 API 和旧 Chat 兼容响应契约保持不变。

### 阶段三：每轮更新、确定性归并与 PostgreSQL 一致性

#### 实施内容

1. 实现纯逻辑 Working Memory Reducer。
2. 根据当前请求、AgentResult、Evidence、Task artifacts、错误和 Review 构造
   `WorkingMemoryUpdate`。
3. 实现事实引用校验、用户声明降级、语义去重、supersede/resolve 和数量裁剪。
4. 抽取 JSON/SSE 共用的运行完成流程。
5. 在 PostgreSQL 完成事务中保存 run、events 和有效记忆更新。
6. 实现 CAS 冲突后的重新加载、重新归并和一次有界重试。
7. 没有可更新材料时不创建空记忆、不增加版本。
8. 扩展 `clear_thread()`，清理工作记忆并真实表达部分删除失败。
9. 日志记录 `run_id/thread_id/memory_version/item_count/duration/status`，不记录正文。

#### 测试要求

- 同一事实重复出现不会生成重复活跃条目。
- 新 Evidence 推翻旧结论时，旧结论被标记为 superseded。
- 工具失败只形成错误/待验证项，不生成“无异常”事实。
- 无有效更新时版本不增加。
- 同一线程两次请求后，第二次完成会产生包含两轮有效材料的新快照。
- 两个并发 run 的 MemoryUpdate 不会静默丢失。
- 首次 CAS 冲突能重新合并；达到重试上限后明确降级。
- run/event/memory 提交失败时事务行为符合设计。
- `run()` 与 `run_stream()` 产生等价的最终工作记忆。
- 客户端中途关闭 SSE 后不更新工作记忆。
- `clear_thread()` 删除运行、事件、Checkpoint 和工作记忆。

#### 验收条件

- 同一 `thread_id` 能完成“加载旧记忆 -> 执行本轮 -> 写入新版本”的闭环。
- JSON 与 SSE 不存在记忆更新行为差异。
- 并发请求不存在无提示最后写入覆盖。
- 记忆更新失败不会把有效主回答伪造成失败，也不会覆盖最后一个合法快照。
- 服务重启后读取的是最后一个已提交版本。

### 阶段四：受约束 LLM 压缩、质量控制与文档

#### 实施内容

1. 使用共享 LLM Factory 增加 memory/compressor 角色配置。
2. 使用 `with_structured_output(WorkingMemoryUpdate)` 实现按阈值触发的语义压缩。
3. 压缩器输入只包含旧的受限摘要、本轮结构化结果、Evidence 摘要、错误和最终结论。
4. 压缩输出必须再次经过引用、时间、条目 ID、长度和数量校验。
5. 实现压缩超时、非法结构和模型失败时的确定性降级。
6. 记录非敏感的加载、裁剪、压缩、冲突和更新指标。
7. 更新 README、架构文档和 API 行为说明，明确能力范围。
8. 如决定新增记忆 SSE 事件，同步更新事件模型、前端和契约测试；否则只保留服务端
   可观察性。

#### 测试要求

- 只有达到配置阈值时才调用压缩模型。
- Fake LLM 返回合法 delta 时能够完成压缩。
- 模型虚构 Evidence ID 时更新被拒绝或降级。
- 模型把用户声明升级为 supported 时更新被拒绝或降级。
- 模型超时、抛错、返回空值或非法结构时主回答正常完成。
- 压缩前后当前目标、有效事实、未完成项和来源引用不丢失。
- 动态 Evidence 在压缩后仍保持观察时间和过期状态。
- 压缩后的 Context Pack 不超过配置预算。
- 文档没有宣称多租户安全、长期用户画像或未经评估的量化指标。

#### 验收条件

- 长线程的工作记忆保持在配置预算内。
- 压缩不会删除必要 Evidence 引用或改变事实验证等级。
- 压缩模型不可用时系统仍能依靠结构化快照和确定性裁剪继续运行。
- 所有可行的单元、集成、契约、lint 和类型检查通过。
- 此时才可以描述为“按 `thread_id` 的 PostgreSQL 跨轮案件工作记忆与上下文压缩”，
  仍不得描述为多租户长期记忆。

---

## 10. 关键端到端验收场景

### 场景一：连续排障

```text
第 1 轮：客户无法登录，帮我排查。
第 2 轮：问题从上午 10 点开始，继续判断是否需要升级。
第 3 轮：告警恢复后，还需要继续按 P1 处理吗？
```

期望：

- 第 1 轮建立案件目标和下一步。
- 第 2 轮加载第 1 轮目标，把“10 点开始”保存为用户声明，而不是已验证事实。
- 第 3 轮保留历史案件信息；涉及“告警恢复后/当前”时重新查询动态数据。
- 最终回答区分历史观察、当前 Evidence、暂时结论和未验证项。

### 场景二：服务重启

1. 第一实例完成一轮请求并关闭。
2. 第二实例使用同一个 PostgreSQL 和相同 `thread_id` 启动。
3. 第二轮发送“继续上面的排查”。

期望：第二实例能加载结构化快照与最近对话；不依赖第一实例的进程内对象或 MemorySaver。

### 场景三：线程隔离

两个不同 `thread_id` 使用相同服务、相似问题并交替请求。

期望：目标、事实、最近对话、Evidence 和下一步完全隔离。

### 场景四：并发请求

同一 `thread_id` 同时补充“影响上海区域”和“开始时间为 10 点”。

期望：通过版本冲突重载和归并，最终记忆同时保留两项；不得只保留最后完成的请求。

### 场景五：动态 Evidence 过期

第一轮查询到当前告警；经过配置 TTL 后，第二轮询问“现在恢复了吗”。

期望：旧告警只作为历史线索，Data Agent 重新查询；工具失败时回答“当前状态未验证”，
不得回答“已经恢复”或“没有告警”。

### 场景六：压缩失败

工作记忆达到压缩阈值，但 Fake Compressor 超时或返回非法 Evidence 引用。

期望：主回答完成；旧合法摘要和确定性更新保留；非法模型输出不进入 PostgreSQL。

---

## 11. 测试分层与执行要求

### 11.1 单元测试

- 工作记忆和 MemoryUpdate 契约。
- Freshness Policy 和 Fake Clock。
- Reducer 去重、覆盖、解决、裁剪和不变量。
- 角色化 Context Renderer 与字符预算。
- 压缩触发器和 Fake Compressor。
- Memory Store Protocol 的 Fake 行为。
- Supervisor/Reviewer 的记忆使用边界。

普通单元测试不得依赖真实 DashScope、Milvus、MCP 或 PostgreSQL。

### 11.2 PostgreSQL 集成测试

- Alembic upgrade 和 Schema 校验。
- Store CRUD、CAS、事务回滚和并发冲突。
- 两个 Service 实例共享工作记忆。
- 服务关闭后重新启动恢复。
- run/event/memory 完成事务。
- 删除线程后的完整清理。

使用现有 PostgreSQL marker 和隔离测试数据库，不连接开发或生产数据库。

### 11.3 Agent 集成测试

- 两轮同线程请求。
- 不同线程隔离。
- 动态 Evidence 过期重新查询。
- MCP 失败不会生成伪成功记忆。
- JSON/SSE 等价更新。
- Task 子图跨 run 隔离。

### 11.4 API 契约测试

- `/api/agent/run` 和 `/run_stream` 响应兼容。
- `/api/chat` 和 `/chat_stream` 兼容。
- SSE 仍只有一个终态。
- 线程查询不意外暴露内部工作记忆。
- 清理接口同时清除记忆，但保持现有响应结构；如需增加字段，必须先明确版本兼容策略。

### 11.5 每阶段检查命令

开发过程中先运行最小相关测试，阶段完成前至少执行：

```bash
make test-quick
make lint
make type-check
```

阶段三和阶段四完成前，在环境允许时执行：

```bash
make test
make check-all
```

PostgreSQL 集成测试如因 `TEST_POSTGRES_URL` 未配置而跳过，必须在交接中明确说明，不能把
跳过描述为通过。

---

## 12. 配置计划

具体名称可在实现时根据现有风格微调，但语义必须集中配置：

```text
WORKING_MEMORY_ENABLED
WORKING_MEMORY_RECENT_TURNS
WORKING_MEMORY_RECENT_MESSAGE_MAX_CHARS
WORKING_MEMORY_CONTEXT_MAX_CHARS
WORKING_MEMORY_SUMMARY_MAX_CHARS
WORKING_MEMORY_MAX_ITEMS
WORKING_MEMORY_MAX_OPEN_QUESTIONS
WORKING_MEMORY_MAX_NEXT_STEPS
WORKING_MEMORY_MAX_EVIDENCE_REFS
WORKING_MEMORY_COMPRESSION_THRESHOLD
WORKING_MEMORY_UPDATE_MAX_RETRIES
WORKING_MEMORY_COMPRESSION_TIMEOUT_SECONDS
```

动态来源 TTL 建议按受控类别配置，而不是让模型决定：

```text
WORKING_MEMORY_ALERT_TTL_SECONDS
WORKING_MEMORY_METRIC_TTL_SECONDS
WORKING_MEMORY_TICKET_TTL_SECONDS
WORKING_MEMORY_DEFAULT_DYNAMIC_TTL_SECONDS
```

配置初始值只是工程上限，需要通过固定测试场景和后续 Eval 调整；在没有数据集前不得声明
其为最佳值。

---

## 13. 清理、保留与安全

### 13.1 清理顺序

`clear_thread(thread_id)` 应执行或协调：

1. 删除工作记忆。
2. 删除 `agent_runs`，由外键级联删除 `agent_events`。
3. 删除该线程每个 `thread_id:run_id` 的顶层 Checkpoint。
4. 返回真实删除结果。

工作记忆、RunStore 和 LangGraph Checkpoint 无法形成跨系统分布式事务。任何部分删除失败都
必须记录结构化错误，不能让用户误以为所有内容已删除。

### 13.2 数据保留

第一阶段不自动定义生产保留天数，但表结构和服务应预留按 `updated_at` 清理已关闭或长期不活跃
线程的能力。自动清理策略需要在认证、案件状态和审计要求明确后单独设计。

### 13.3 安全边界

- 文档内容、用户消息、Evidence 和工具输出全部是不可信输入。
- 记忆正文不得进入普通 info 日志。
- 数据库错误对外必须清洗，不能包含 SQL、DSN、内部主机或凭据。
- `query_scope` 不保存 Token、Cookie、Authorization 或未经限制的调用参数。
- 当前 `owner_user_id` 不是认证凭证。
- 在认证和租户授权完成前，功能只能描述为开发/单组织场景下按 `thread_id` 隔离。

---

## 14. 文档与交付要求

每个阶段完成后，应在 `xzh_docs/handoff_docs` 新增对应交接文档，至少记录：

- 实际修改文件。
- 实际完成和未完成能力。
- 数据库 migration revision。
- 配置变化。
- 执行的测试命令与结果。
- PostgreSQL 集成测试是否真实运行。
- 已知限制和下一阶段入口。
- `git status --short` 中的预期修改。

实现改变重要依赖方向或公共行为时，同步更新 README 和架构文档。文档只能描述已经由代码和
测试证明的能力。

---

## 15. 最终完成标准

只有同时满足以下条件，整个计划才可标记为完成：

- PostgreSQL 工作记忆 Schema、Store 和迁移已落盘并通过真实集成测试。
- 同一 `thread_id` 能跨 Service 实例和进程重启恢复。
- 最近对话读取有 SQL limit 和内容预算，不加载完整线程进入 Prompt。
- Supervisor、Specialist 和 Reviewer 只接收职责所需的最小上下文。
- 用户声明、Evidence 观察和结论被明确区分。
- 动态 Evidence 有观察时间、查询范围和过期判断。
- 每轮更新使用确定性 Reducer，LLM 只能提出受约束 delta。
- 并发更新使用版本控制和有限冲突重试，不静默覆盖。
- JSON 与 SSE 使用同一完成逻辑，并保持一个终态事件。
- 客户端断开不会保存半完成记忆。
- 线程清理包含工作记忆，失败不会被伪装成成功。
- 压缩失败不影响有效主回答，也不破坏最后一个合法快照。
- 统一 API 和旧 Chat 兼容契约通过测试。
- `/api/aiops` 未被错误描述为已经接入工作记忆。
- 文档明确说明当前不是长期用户画像，也不是多租户安全记忆。
