# 企业知识服务多智能体平台改造计划

> 基于当前 `Oncall_agent` Python 项目的渐进式改造方案  
> 目标架构：Supervisor-Specialist Multi-Agent + LangGraph Custom Workflow + RAG + MCP + Reviewer 质量闭环  
> 文档状态：渐进实施中的架构路线图
> 适用目标：Agent 应用开发岗位的项目建设、演示与面试讲解

> 实施进度（2026-08-23）：M1（阶段 0～1）、M2（阶段 2～4）、M3（阶段 5～6）
> 与 M4（阶段 7）已完成并通过离线测试、Ruff 和 Mypy 验收。实际变更与限制见
> `xzh_docs/handoff_docs/2026-08-23-m1-implementation-handoff.md` 与
> `xzh_docs/handoff_docs/2026-08-23-m2-implementation-handoff.md`、
> `xzh_docs/handoff_docs/2026-08-23-m3-implementation-handoff.md`、
> `xzh_docs/handoff_docs/2026-08-23-m4-implementation-handoff.md`。M5 仍为计划中。

## 1. 改造结论

本项目不建议更换为 AutoGen、CrewAI 等新框架，也不建议直接把现有 Planner、Executor、Replanner 三个节点包装成三个 Agent。最合适的路线是在现有 LangChain、LangGraph、Milvus、MCP 和 FastAPI 基础上，演进为以下混合架构：

```text
                                  ┌──────────────────────────┐
                                  │     Knowledge Agent      │
                                  │ Milvus 检索、证据与引用   │
                                  └────────────┬─────────────┘
                                               │
用户请求 ──> 输入规范化 ──> Supervisor Agent ──┼──> 结果聚合 ──> Reviewer Agent ──> 最终响应
                            │                  │                              │
                            │                  │                              ├─ 通过：END
                            │                  │                              └─ 不通过：有限返工
                            │                  │
                            │       ┌──────────┴──────────┐
                            │       │      Data Agent      │
                            │       │ MCP / 外部数据查询   │
                            │       └─────────────────────┘
                            │
                            └──────> Task Agent 子图
                                     Planner -> Executor -> Replanner
```

其中：

- **Supervisor Agent** 是唯一拥有全局会话视角的协调者，负责路由、委派、结果聚合和终止决策。
- **Knowledge Agent** 只负责企业知识库检索、证据整理和基于证据回答。
- **Data Agent** 只负责通过 MCP 或受控本地工具查询外部数据，不负责自由发挥生成结论。
- **Task Agent** 是现有 Plan-Execute-Replan 工作流演化后的复合 Agent，负责多步骤复杂任务。
- **Reviewer Agent** 负责检查答案的完整性、证据支撑、工具错误和任务遗漏，并控制有限次数返工。
- Planner、Executor、Replanner 保持为 Task Agent 内部节点，不把工作流节点虚构为独立 Agent。

第一版建议实现 5 个协作单元：Supervisor、Knowledge、Data、Task、Reviewer。其中 Task Agent 不是单次 LLM 调用，而是包含 Planner、Executor、Replanner 多个节点的复合 Agent/子图。这样既能体现 Multi-Agent，又能保证职责边界清晰、工程解释成立。

---

## 2. 当前项目基线

### 2.1 已有能力

| 能力 | 当前实现 | 可复用程度 |
|---|---|---:|
| FastAPI 接口 | `app/main.py`、`app/api/chat.py`、`app/api/aiops.py` | 高 |
| 通用对话 Agent | `app/services/rag_agent_service.py` | 中，需要拆分职责 |
| RAG 检索 | `app/tools/knowledge_tool.py`、Milvus 相关 Service | 高 |
| 文档入库 | `app/api/file.py`、`vector_index_service.py` | 高 |
| Plan-Execute-Replan | `app/services/aiops_service.py`、`app/agent/aiops/` | 高，需去 AIOps 化 |
| MCP 客户端 | `app/agent/mcp_client.py` | 高，需增加注册与权限层 |
| 本地工具 | `app/tools/` | 中，需要按 Agent 分组 |
| 会话状态 | LangGraph `MemorySaver` + `thread_id` | 中，仅适合单进程演示 |
| 流式输出 | SSE + LangGraph stream | 高，需要统一事件模型 |
| 前端 | `static/index.html`、`static/app.js` | 中，当前绑定 chat/AIOps 两套事件 |

### 2.2 当前架构的主要问题

1. **Agent 职责过宽。** `RagAgentService` 同时绑定知识、时间、Prometheus 和 MCP 工具，模型在一个上下文里完成所有决策，缺少专家边界。
2. **不存在统一协调者。** `/chat` 和 `/aiops` 分别进入两套服务，用户需要提前选择模式，系统不能自动判断应该问答、检索还是执行复杂任务。
3. **Plan-Execute-Replan 仍绑定运维语义。** 类名、接口名、提示词、报告格式、前端 UI 和示例数据大量使用 AIOps/告警/诊断概念。
4. **工具没有权限域。** `DEFAULT_LOCAL_AGENT_TOOLS` 被多个模块直接使用，不同 Agent 无法拥有不同工具白名单。
5. **Agent 间没有结构化交接。** 当前传递的是字符串或完整消息，无法稳定表达任务目标、证据、错误、来源和期望输出。
6. **状态不持久。** `MemorySaver` 在进程退出后丢失，多 Worker 之间不共享，暂时不能表述为生产级持久化会话。
7. **缺少质量闭环。** 没有 Reviewer、引用完整性检查、结果返工或可量化 Eval。
8. **事件协议分裂。** `/chat_stream` 与 `/aiops` 使用不同字段和处理逻辑，前端存在重复的 SSE 解析代码。
9. **缺少测试基线。** `pyproject.toml` 配置了 pytest，但仓库当前未发现测试文件，重构风险较高。
10. **部分注释与实现不一致。** 例如消息裁剪函数已定义但未接入 Agent middleware；简历不能据此宣称已经实现摘要或 Token 优化。

### 2.3 改造期间必须保持的能力

- 文档上传和 Milvus 索引继续可用。
- `/api/chat`、`/api/chat_stream`、`/api/aiops` 在迁移完成前保持兼容。
- MCP 服务不可用时，知识问答仍可以降级运行。
- SSE 连接出现局部节点错误时必须返回结构化错误，而不是静默断流。
- 所有 Agent 均使用统一的 LLM 工厂、工具注册表和运行配置，避免每个节点重复创建模型和加载 MCP 工具。

---

## 3. 目标业务定位

### 3.1 项目名称

推荐名称：**企业知识服务多智能体协同平台**。

不再以 OnCall 或 AIOps 作为项目主名称。运维告警可以保留为一个 MCP 数据源示例，但不是项目的唯一业务场景。

### 3.2 核心使用场景

1. **知识问答：** 查询制度、产品资料、项目规范、FAQ 等内部文档。
2. **多源查询：** 同时查询知识库和 MCP 外部数据，并聚合为带来源的回答。
3. **复杂任务：** 将“分析问题并给出处理方案”类请求拆成步骤，动态执行和重规划。
4. **质量校验：** 对复杂回答检查证据、完整性和工具失败，必要时触发一次返工。

### 3.3 非目标

第一阶段不实现以下内容，避免范围失控：

- Agent 之间自由群聊或无限轮讨论。
- 自动执行具有破坏性的业务操作。
- 动态生成任意 Agent。
- 长期用户画像和自动记忆提取。
- 微服务化拆分每个 Agent。
- 为了框架名而同时引入 LangGraph、AutoGen、CrewAI 多套运行时。

### 3.4 开发实现原则

本次改造必须把代码干净、简洁和可读作为验收条件，而不是只关注功能是否跑通。项目用于学习和面试讲解，代码应让阅读者能够沿着正常执行路径快速理解 Agent 职责、状态变化和图路由。

- 优先采用 Python、FastAPI、Pydantic、LangChain 和 LangGraph 的标准惯用语法，不为展示技巧引入自定义 DSL、元编程或复杂框架包装。
- 先写清晰的正常流程，再为真实存在的失败边界增加兜底。适合设置兜底的位置包括 LLM、MCP、Milvus、网络、文件输入和持久化存储。
- 兜底代码必须对应明确故障，并使失败可观察；不得通过静默吞异常、返回空字符串或伪造成功状态继续运行。
- 避免层层 `isinstance`、`getattr`、空值检查和宽泛 `try/except`。内部模块之间使用 Pydantic 模型、类型标注和不变量建立信任，在系统边界集中校验一次。
- 只有当前层确实能够恢复或降级时才捕获异常；否则补充必要上下文后向上抛出，交给 API、工作流或外部服务边界处理。
- 避免过早抽象、无意义的 Manager/Wrapper/Helper、单次转发层和重复兼容代码。公共抽象必须具有稳定职责和至少两个真实调用方。
- 每个函数和模块保持单一职责，优先使用准确命名表达意图。注释解释“为什么这样设计”，不逐行复述代码。
- 兼容逻辑集中放在旧 API 适配层，不把新旧格式判断扩散到核心 Agent、状态和工具实现中。
- 简洁实现不能省略必要安全边界。工具白名单、输入校验、超时、有限重试、循环终止、密钥保护和写操作审批仍必须保留。
- Code Review 时如果一段防御代码无法对应一个可复现故障、明确外部边界或测试用例，应优先删除或简化。

---

## 4. 目标代码结构

建议逐步调整为以下目录。旧文件在兼容期保留，待新链路稳定后再删除或改为适配器。

```text
app/
├── agent/
│   ├── graph.py                       # 顶层 Multi-Agent LangGraph
│   ├── state.py                       # 全局共享状态
│   ├── contracts.py                   # AgentTask、AgentResult、Evidence 等契约
│   ├── registry.py                    # Agent 注册表与能力描述
│   ├── supervisor/
│   │   ├── agent.py                   # Supervisor 决策逻辑
│   │   ├── prompt.py
│   │   └── schemas.py                 # RouteDecision 等结构化输出
│   ├── specialists/
│   │   ├── knowledge/
│   │   │   ├── agent.py
│   │   │   ├── prompt.py
│   │   │   └── schemas.py
│   │   ├── data/
│   │   │   ├── agent.py
│   │   │   ├── prompt.py
│   │   │   └── schemas.py
│   │   ├── reviewer/
│   │   │   ├── agent.py
│   │   │   ├── prompt.py
│   │   │   └── schemas.py
│   │   └── task/
│   │       ├── graph.py               # Task Agent 子图
│   │       ├── state.py
│   │       ├── planner.py
│   │       ├── executor.py
│   │       └── replanner.py
│   └── runtime/
│       ├── context.py                 # run_id/user_id/thread_id 等运行上下文
│       ├── checkpointer.py            # Memory/SQLite/Postgres Checkpointer 工厂
│       └── limits.py                  # 最大步骤、返工和工具调用限制
├── tools/
│   ├── registry.py                    # 工具注册、分类和权限白名单
│   ├── knowledge_tool.py
│   ├── time_tool.py
│   ├── query_metrics_alerts.py
│   └── mcp_provider.py                # MCP 工具加载与缓存适配
├── services/
│   ├── multi_agent_service.py         # 顶层调用、流式事件转换
│   ├── event_service.py               # 统一内部事件到 SSE 事件的转换
│   ├── rag/                            # 后续可再拆分，首期可保留现状
│   └── ...
├── api/
│   ├── agent.py                       # 新统一入口
│   ├── chat.py                        # 旧接口兼容适配
│   ├── aiops.py                       # 旧接口兼容适配，后续废弃
│   └── file.py
├── models/
│   ├── agent.py                       # 新 API 请求/响应/SSE 模型
│   └── ...
└── evals/
    ├── datasets/
    ├── evaluators/
    └── runner.py

tests/
├── unit/
├── integration/
├── contract/
└── evals/
```

---

## 5. 核心领域模型与状态设计

### 5.1 运行标识

需要区分三个概念：

| 字段 | 含义 | 生命周期 |
|---|---|---|
| `user_id` | 用户身份 | 长期 |
| `thread_id` | 一段多轮会话 | 多个请求 |
| `run_id` | 一次 Agent 执行 | 单个请求 |

当前 `session_id` 可在兼容层映射为 `thread_id`。不能继续把一次任务执行和一段用户会话完全混为一体。

### 5.2 Agent 任务契约

建议在 `app/agent/contracts.py` 中定义 Pydantic 模型：

```python
class AgentTask(BaseModel):
    task_id: str
    objective: str
    context: str = ""
    expected_output: str
    constraints: list[str] = Field(default_factory=list)
    preferred_tools: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    source_type: Literal["knowledge", "mcp", "tool"]
    source_name: str
    source_id: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    agent_name: str
    task_id: str
    status: Literal["success", "partial", "failed"]
    summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
```

Agent 之间主要传递这些结构化对象，而不是传递全部内部推理消息。这样可以控制上下文长度、保留可展示证据并降低不同 Agent 之间的耦合。

### 5.3 顶层状态

建议在 `app/agent/state.py` 中定义：

```python
class MultiAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    user_id: str
    thread_id: str
    run_id: str
    user_query: str
    route: str
    pending_tasks: list[AgentTask]
    agent_results: Annotated[list[AgentResult], operator.add]
    execution_plan: list[str]
    review_result: dict[str, Any] | None
    retry_count: int
    final_answer: str
    status: str
```

注意：

- `agent_results` 使用 Reducer 追加，避免并行 Agent 结果互相覆盖。
- `pending_tasks` 和 `execution_plan` 使用覆盖语义。
- `messages` 只保留用户可见的主会话历史；专家 Agent 的内部消息放在各自子图状态中。
- 不在状态中保存模型 Chain-of-Thought，只保存路由结果、工具调用、证据、错误和可展示摘要。

### 5.4 Supervisor 结构化输出

```python
class RouteDecision(BaseModel):
    mode: Literal["direct", "single", "parallel", "complex"]
    target_agents: list[Literal["knowledge", "data", "task"]]
    tasks: list[AgentTask]
    reason_summary: str
```

`reason_summary` 只描述可展示的路由依据，例如“问题同时需要内部文档和实时数据”，不要求模型暴露详细思维链。

### 5.5 Reviewer 输出

```python
class ReviewResult(BaseModel):
    passed: bool
    completeness_score: float
    groundedness_score: float
    issues: list[str]
    retry_target: Literal["none", "knowledge", "data", "task", "supervisor"]
    retry_instruction: str = ""
```

限制 `retry_count <= 1`，第一版不要允许 Reviewer 无限返工。

---

## 6. Agent 职责与工具权限

### 6.1 Supervisor Agent

**职责：**

- 分析请求是否需要外部能力。
- 生成结构化子任务。
- 选择单 Agent、并行 Agent 或 Task Agent 子图。
- 聚合多个 Agent 的结果。
- 根据 Reviewer 反馈决定是否返工。

**允许工具：**

- `dispatch_knowledge_agent`
- `dispatch_data_agent`
- `dispatch_task_agent`

Supervisor 不直接访问 Milvus、Prometheus 或日志 MCP，防止协调者变成另一个全能 Agent。

### 6.2 Knowledge Agent

**职责：**

- 生成适合检索的查询。
- 调用 Milvus 检索。
- 整理来源、标题、文档片段和相关性信息。
- 仅依据检索证据生成结果；无证据时明确返回不足。

**允许工具：**

- `retrieve_knowledge`
- 可选 `list_knowledge_sources`

**需要修改的现有实现：**

- `retrieve_knowledge` 不再只返回拼接文本，应把原始 `Document` 转换为可序列化 `Evidence`。
- 保留 `_file_name`、`_source`、标题层级等元数据。
- 给回答返回引用编号，例如 `[来源1]`，为 Reviewer 检查提供基础。

### 6.3 Data Agent

**职责：**

- 根据任务选择 MCP 或本地外部数据工具。
- 校验参数并执行只读查询。
- 将工具原始结果转换为结构化证据。
- 工具失败时返回 `partial/failed`，不能用模型补造数据。

**允许工具：**

- MCP 只读查询工具。
- `query_prometheus_alerts` 等受控本地数据工具。
- `get_current_time` 仅在时间语义必要时开放。

**首期策略：**

- 现有 CLS/Monitor MCP 可保留为演示数据源。
- 对外统一描述为“外部业务数据查询”，运维数据只是示例实现。
- 为 MCP 工具增加 `category`、`read_only`、`timeout`、`allowed_agents` 元数据。

### 6.4 Task Agent 子图

**职责：**

- 接收复杂目标和约束。
- 生成有限步骤计划。
- 每次只执行一个步骤。
- 根据已有证据判断继续、重规划或结束。
- 输出执行摘要、证据和失败步骤。

**改造重点：**

- 将 `app/agent/aiops/` 迁移到 `app/agent/specialists/task/`。
- 将 `PlanExecuteState.input` 改为结构化 `AgentTask` 或至少新增 `task_id/objective`。
- 删除提示词中的“当前系统告警”“诊断报告”等强运维语义。
- Executor 不再直接获取全部工具，而是通过 Tool Registry 按当前步骤和权限获取工具。
- Planner 不要在规划阶段重复访问知识库和反复加载 MCP 工具列表；工具能力描述由 Registry 注入。
- `MAX_STEPS` 移入配置，并记录终止原因。
- 子图最终返回 `AgentResult`，由 Supervisor 决定如何展示。

### 6.5 Reviewer Agent

**职责：**

- 检查回答是否覆盖原始目标。
- 检查关键结论是否能映射到 `Evidence`。
- 检查工具错误是否被如实披露。
- 检查是否混入未查询到的事实或数字。
- 给出结构化通过/返工结果。

**允许工具：** 默认无工具。

Reviewer 的输入只包括用户问题、候选答案、证据摘要和错误列表，避免重新执行任务造成成本失控。

### 6.6 工具权限矩阵

| 工具类别 | Supervisor | Knowledge | Data | Task | Reviewer |
|---|:---:|:---:|:---:|:---:|:---:|
| Milvus 检索 | 否 | 是 | 否 | 通过 Knowledge Agent | 否 |
| MCP 查询 | 否 | 否 | 是 | 通过 Data Agent，必要时受控直调 | 否 |
| 时间工具 | 否 | 可选 | 是 | 可选 | 否 |
| Agent 调度工具 | 是 | 否 | 否 | 可选 | 否 |
| 写操作工具 | 否 | 否 | 首期禁止 | 首期禁止 | 否 |

---

## 7. 顶层 LangGraph 设计

### 7.1 节点

| 节点 | 输入 | 输出 |
|---|---|---|
| `normalize_input` | API 请求 | 标准化用户、会话、运行信息 |
| `supervisor` | 主状态与已有结果 | `RouteDecision`、待执行任务 |
| `knowledge_agent` | 知识子任务 | `AgentResult` |
| `data_agent` | 数据子任务 | `AgentResult` |
| `task_agent` | 复杂任务 | `AgentResult` |
| `aggregate` | 所有 AgentResult | 候选最终答案 |
| `reviewer` | 候选答案与证据 | `ReviewResult` |
| `finalize` | 审核结果 | 最终响应与完成事件 |
| `handle_error` | 节点异常 | 可读错误与降级结果 |

### 7.2 路由

```text
START -> normalize_input -> supervisor

supervisor:
  direct   -> aggregate
  single   -> 指定 specialist -> aggregate
  parallel -> Send(knowledge/data/...) -> aggregate
  complex  -> task_agent -> aggregate

aggregate -> reviewer

reviewer:
  passed=true                       -> finalize -> END
  passed=false and retry_count < 1  -> supervisor
  passed=false and retry_count >= 1 -> finalize（披露不足）-> END
```

### 7.3 简单任务与复杂任务判定

Supervisor 可采用结构化 LLM 路由，但增加代码级兜底：

- 普通闲聊、解释性问题：`direct`。
- 明确询问内部资料：`single -> knowledge`。
- 明确询问实时数据或外部系统：`single -> data`。
- 同时需要文档与外部数据且二者无依赖：`parallel`。
- 目标包含多个依赖步骤、需要基于中间结果调整：`complex -> task`。

第一版先支持上述有限枚举，不允许模型生成任意节点名称。

### 7.4 并行执行

知识查询与外部数据查询互不依赖时，可使用 LangGraph `Send` 扇出。聚合时必须：

- 使用 `agent_results` Reducer 追加结果。
- 每个结果携带唯一 `task_id`。
- 设置单 Agent 超时。
- 一个 Agent 失败时允许另一个 Agent 的结果继续进入聚合。
- 聚合结果按任务 ID 去重，防止重试重复追加。

首期只并行 Knowledge 与 Data 两类只读任务，不并行执行具有依赖关系的 Task Agent 步骤。

### 7.5 终止保障

必须同时设置：

- Supervisor 最大调度轮次，例如 4。
- Task Agent 最大步骤数，例如 6 或 8。
- Reviewer 最大返工次数 1。
- 单工具调用超时与 MCP 重试次数。
- 图递归上限 `recursion_limit`。
- 明确的 `status`：`running/completed/partial/failed`。

以上数值是配置项和验收目标，不作为已经实现的性能数据写入简历。

---

## 8. LLM、工具与运行时基础设施改造

### 8.1 统一 LLM Factory

当前 Planner、Executor、Replanner 和 RagAgentService 分别实例化 `ChatQwen`。应扩展 `app/core/llm_factory.py`：

- 根据角色创建模型：`supervisor`、`specialist`、`reviewer`。
- 统一设置模型名、temperature、timeout、streaming。
- 支持结构化输出与普通对话两类配置。
- 便于测试时注入 Fake Model。
- 记录模型角色、调用耗时和 Token 使用量（供应商返回时）。

建议配置：

- Supervisor：温度 0，结构化输出。
- Knowledge/Data：温度 0 或较低。
- Task Planner/Replanner：温度 0，结构化输出。
- 最终聚合：低温度。
- Reviewer：温度 0，结构化输出。

### 8.2 Tool Registry

新增 `app/tools/registry.py`，统一解决当前工具散落和重复加载问题：

```python
class ToolSpec(BaseModel):
    name: str
    category: str
    read_only: bool
    allowed_agents: set[str]
    timeout_seconds: float
    source: Literal["local", "mcp"]
```

Registry 至少提供：

- `get_tools_for_agent(agent_name)`
- `get_tool_descriptions(agent_name)`
- `refresh_mcp_tools()`
- `health()`
- 工具名称冲突检测。

`DEFAULT_LOCAL_AGENT_TOOLS` 在兼容期保留，但新代码不再直接依赖它。

### 8.3 MCP 生命周期

保留现有指数退避和安全加载逻辑，并增加：

- MCP 工具清单缓存，避免每个 Planner/Executor 节点都调用 `get_tools()`。
- 启动时异步预热，失败不阻塞知识问答。
- 每个 MCP Server 独立健康状态。
- 工具调用超时和错误分类。
- 原始 MCP 错误只写服务端日志，对模型返回经过清洗的错误。
- 记录 `server_name/tool_name/duration/status/run_id`。

### 8.4 Checkpointer

分两步实施：

1. 首期仍使用 `MemorySaver`，但通过 Checkpointer Factory 注入，不在每个 Service 内部直接实例化。
2. Multi-Agent 主链路稳定后增加 SQLite 开发实现或 PostgreSQL 生产实现，使状态可跨进程恢复。

验收时需要测试：

- 相同 `thread_id` 可以读取历史。
- 不同 `thread_id` 不串话。
- 相同 thread 的两个 run 不会错误合并 `agent_results`。
- 服务重启后，持久实现能够恢复会话。

### 8.5 上下文管理

当前 `trim_messages_middleware` 未实际接入。改造后应明确：

- Supervisor 保留最近若干轮用户可见对话。
- 超出阈值后使用“最近消息 + 历史摘要”，或者首期只做可测试的消息裁剪。
- Specialist 默认只接收 `AgentTask.context`、必要的用户问题和证据，不接收完整会话。
- Tool 原始大结果先在代码层截断和结构化，再进入模型上下文。
- 文档引用正文与元数据分开存储，减少重复 Token。

如果只实现裁剪，简历就只写“上下文裁剪”；只有真正实现摘要后，才能写“对话摘要机制”。

---

## 9. API 与 SSE 协议

### 9.1 新统一接口

新增：

```http
POST /api/agent/run
POST /api/agent/run_stream
GET  /api/agent/runs/{run_id}
GET  /api/agent/threads/{thread_id}
DELETE /api/agent/threads/{thread_id}
```

请求模型：

```json
{
  "user_id": "user-001",
  "thread_id": "thread-001",
  "message": "结合内部产品手册和当前数据分析问题原因",
  "stream": true
}
```

响应至少包含：

```json
{
  "run_id": "run-001",
  "thread_id": "thread-001",
  "status": "completed",
  "answer": "...",
  "sources": [],
  "agents": ["knowledge", "data", "reviewer"]
}
```

### 9.2 统一 SSE 事件模型

建议所有事件使用统一 envelope：

```json
{
  "event_id": "evt-001",
  "run_id": "run-001",
  "thread_id": "thread-001",
  "type": "agent_started",
  "agent": "knowledge",
  "timestamp": "2026-08-23T10:00:00+08:00",
  "data": {}
}
```

首期事件类型：

| 类型 | 用途 |
|---|---|
| `run_started` | 一次运行开始 |
| `route_decided` | Supervisor 路由完成 |
| `agent_started` | 专家 Agent 开始 |
| `agent_completed` | 专家 Agent 完成 |
| `plan_created` | Task Agent 创建计划 |
| `step_started` | 开始执行步骤 |
| `tool_started` | 工具开始调用 |
| `tool_completed` | 工具调用完成 |
| `step_completed` | 步骤执行完成 |
| `review_completed` | Reviewer 完成检查 |
| `content_delta` | 最终答案文本增量 |
| `run_completed` | 执行完成 |
| `run_failed` | 执行失败 |

事件中只返回允许展示的参数和结果摘要，API Key、完整异常栈、敏感工具响应不得发送到前端。

### 9.3 旧接口兼容

- `/api/chat` 和 `/api/chat_stream` 内部转发到 `MultiAgentService`，保留旧字段映射。
- `/api/aiops` 暂时构造一个固定复杂任务调用 Task Agent，标记为 deprecated。
- 前端迁移完成并稳定后，再决定是否删除 `/api/aiops`。
- 兼容层必须有契约测试，避免重构后旧页面无法使用。

---

## 10. 前端改造

当前前端将普通聊天和 AIOps 分成两套 SSE 解析路径。建议改为统一任务时间线：

```text
用户问题
  ├─ Supervisor：已识别为“知识 + 数据联合查询”
  ├─ Knowledge Agent：检索到 3 条资料
  ├─ Data Agent：完成 2 个工具调用
  ├─ Reviewer：检查通过
  └─ 最终回答 + 来源
```

需要修改：

- `static/app.js`：抽出统一 `SSEEventParser` 和事件分发器。
- `static/index.html`：将“AIOps”入口改为“复杂任务”或取消手动模式，让 Supervisor 自动路由。
- `static/styles.css`：增加 Agent 标签、并行任务状态、证据卡片和失败状态。
- 最终答案区域展示来源列表，但不展示内部思维链。
- 调试模式下可展示 `run_id`、路由、工具名称和节点耗时。

第一版前端只需展示事件时间线、最终答案、来源和错误；图形化 DAG、Token 曲线等不属于首期范围。

---

## 11. 分阶段实施计划

### 阶段 0：建立测试基线

**目标：** 在改变架构前锁定现有可用行为。

**任务：**

- 创建 `tests/` 目录结构。
- 为文档切分、知识工具格式化、MCP 错误清洗编写单元测试。
- 为 `/api/chat`、`/api/chat_stream`、`/api/aiops` 建立最小契约测试。
- 使用 Fake LLM、Fake Retriever、Fake MCP Tool，避免单元测试依赖真实 DashScope/Milvus/MCP。
- 保存 10～20 条代表性请求作为初始 Eval 数据，不设置虚假成绩。

**验收标准：**

- 测试可在不配置真实 API Key 的情况下运行。
- 现有接口字段和关键 SSE 事件有自动化断言。
- 记录当前失败用例，作为后续改进基线。

### 阶段 1：统一基础设施

**目标：** 消除 Agent 实现之间重复创建模型、重复加载工具的问题。

**任务：**

- 完善 `app/core/llm_factory.py`。
- 新增 Tool Registry 和 Agent 工具权限配置。
- 为 MCP 增加缓存、健康状态和统一错误模型。
- 新增运行限制配置：最大步骤、最大返工、超时和递归上限。
- 新增 Checkpointer Factory，默认仍使用 MemorySaver。

**验收标准：**

- Planner、Executor、Replanner 不再直接初始化 `ChatQwen`。
- 新模块可按 Agent 名称返回不同工具集合。
- MCP 全部不可用时，Knowledge Agent 所需本地能力仍能启动。

### 阶段 2：抽取 Knowledge Agent

**目标：** 从现有通用 RAG Agent 中形成第一个职责明确的 Specialist。

**任务：**

- 创建 Knowledge Agent prompt、schema 和运行类。
- 将知识检索结果转换为 `Evidence`。
- 输出带来源编号的 `AgentResult`。
- 增加无结果、Milvus 异常、低相关结果的处理。
- 为引用格式与证据完整性编写测试。

**验收标准：**

- Knowledge Agent 只能访问知识工具。
- 每条知识性结论都能关联至少一个来源，或明确声明证据不足。
- 不依赖 MCP 服务即可完成知识问答。

### 阶段 3：抽取 Data Agent

**目标：** 将 MCP 和外部数据查询从通用 Agent 中隔离。

**任务：**

- 创建 Data Agent。
- 通过 Tool Registry 注入只读 MCP/本地查询工具。
- 统一工具结果到 `Evidence`。
- 加入超时、重试后失败、部分成功和敏感字段过滤。
- 保留 CLS/Monitor 作为示例 MCP Server，逐步增加一个非运维业务数据示例。

**验收标准：**

- Data Agent 不访问 Milvus。
- 工具失败不会被模型改写成成功结果。
- MCP 服务部分不可用时能够返回 `partial`。

### 阶段 4：将 AIOps 工作流泛化为 Task Agent 子图

**目标：** 保留现有复杂任务能力，去除对运维场景的硬编码。

**任务：**

- 迁移并重命名 `app/agent/aiops/`。
- 泛化 `PlanExecuteState`。
- 重写 Planner、Executor、Replanner 提示词。
- 将工具查询委派给 Knowledge/Data Agent，或由 Registry 提供严格白名单。
- 删除 `diagnose()` 中固定告警任务与固定诊断报告模板，改为通用 `execute(task)`。
- 保留旧 `diagnose()` 作为兼容适配器。

**验收标准：**

- 至少跑通三类非运维复杂任务。
- 每个步骤都有开始、完成、失败事件。
- 到达最大步骤后能够生成 `partial` 结果并正常终止。

### 阶段 5：实现 Supervisor 与顶层图

> 实施状态（2026-08-23）：已完成，验收证据见 M3 交接文档。

**目标：** 形成真实 Multi-Agent 主链路。

**任务：**

- 定义 MultiAgentState、RouteDecision 和顶层 StateGraph。
- 实现 direct/single/parallel/complex 四种路由。
- 将三个 Specialist 作为独立节点或工具接入 Supervisor。
- 实现结果聚合和错误降级。
- 记录 Agent 调度轨迹与耗时。

**验收标准：**

- 用户无需手动选择 chat/AIOps 模式。
- Supervisor 可以正确调用 Knowledge、Data 或 Task Agent。
- Knowledge 与 Data 可在无依赖场景并行执行。
- 任一 Specialist 失败不会造成无限循环。

### 阶段 6：增加 Reviewer 质量闭环

> 实施状态（2026-08-23）：已完成，验收证据见 M3 交接文档。

**目标：** 对复杂回答增加可控的质量检查。

**任务：**

- 实现 Reviewer 结构化输出。
- 增加证据引用、完整性、工具错误披露检查。
- 实现最多一次定向返工。
- 记录审核前后结果，用于 Eval 对比。

**验收标准：**

- Reviewer 不能直接修改工具事实。
- 一次返工后必须结束。
- 对无证据数字、遗漏工具错误等预设坏样例能够判定不通过。

### 阶段 7：统一 API、SSE 与前端

> 实施状态（2026-08-23）：已完成，验收证据见 M4 交接文档。

**目标：** 对外形成一个多智能体产品入口。

**任务：**

- 新增 `/api/agent/run` 和 `/api/agent/run_stream`。
- 实现统一事件 envelope。
- 旧接口转发新 Service。
- 重构前端 SSE 解析器和 Agent 执行时间线。
- 更新页面名称、文案和模式入口。

**验收标准：**

- 新接口能够覆盖简单问答、知识检索、多源查询和复杂任务。
- 流式断线、节点失败和正常完成均有明确终态事件。
- 旧接口契约测试继续通过。

### 阶段 8：持久化、可观测性与 Eval

**目标：** 补齐 Agent 应用工程化能力。

**任务：**

- 接入持久化 Checkpointer。
- 为每次运行记录 `run_id/thread_id/agent/node/tool/duration/status`。
- 建立路由、检索、工具选择、任务完成和 Reviewer 的 Eval 数据集。
- 输出可重复运行的评估报告。
- 增加超时率、失败率、平均步骤数、模型调用次数和 Token 成本统计。

**验收标准：**

- 服务重启后能恢复指定 thread。
- 一次请求的 Agent 与工具执行链路可以通过 `run_id` 还原。
- Eval 报告能够比较改造前后结果，而不是只展示人工挑选案例。

### 阶段 9：业务资料与项目文档换皮

**目标：** 让代码、演示数据、README 和简历叙述保持一致。

**任务：**

- 将 `aiops-docs/` 替换或扩充为产品手册、业务规范、FAQ、交付指南等资料。
- 增加一个非运维 MCP 数据源示例，例如工单查询或产品信息查询。
- 更新项目包名、README、FastAPI 描述和页面标题。
- 绘制真实架构图、执行时序图和演示用例。
- 完成后再编写最终简历描述和面试问答。

**验收标准：**

- README 中每项能力都有可运行命令或演示请求。
- 简历中的每个关键词都能指向代码、测试、评估或演示证据。
- 不再把未完成的摘要、持久化、并发量或准确率写成既有成果。

---

## 12. 文件级迁移清单

| 当前文件 | 计划动作 | 目标位置/说明 |
|---|---|---|
| `app/services/rag_agent_service.py` | 拆分 | Knowledge Agent + MultiAgentService；旧类保留兼容期 |
| `app/services/aiops_service.py` | 泛化 | `specialists/task/graph.py`，旧 Service 变兼容适配器 |
| `app/agent/aiops/state.py` | 泛化状态 | `specialists/task/state.py` |
| `app/agent/aiops/planner.py` | 去运维语义、统一 LLM | `specialists/task/planner.py` |
| `app/agent/aiops/executor.py` | 接入 Registry/Agent 委派 | `specialists/task/executor.py` |
| `app/agent/aiops/replanner.py` | 结构化终止原因 | `specialists/task/replanner.py` |
| `app/agent/mcp_client.py` | 保留并增强 | 增加缓存、健康状态、超时和错误类型 |
| `app/tools/__init__.py` | 取消全局默认工具依赖 | 由 `tools/registry.py` 按 Agent 分配 |
| `app/tools/knowledge_tool.py` | 增强 | 返回可序列化证据和引用元数据 |
| `app/core/llm_factory.py` | 重构 | 所有 Agent/节点统一创建模型 |
| `app/api/chat.py` | 兼容适配 | 内部调用 MultiAgentService |
| `app/api/aiops.py` | 标记废弃 | 保留固定复杂任务适配，后续删除 |
| `app/api/file.py` | 保留并补测试 | 后续增加文档状态与来源管理 |
| `app/models/request.py` | 保留兼容 | 新模型放 `app/models/agent.py` |
| `static/app.js` | 重构 | 统一 SSE 解析与事件状态机 |
| `static/index.html` | 重命名产品与入口 | 移除强 AIOps 主入口 |
| `README.md` | 最后更新 | 与最终可运行能力一致 |

迁移过程中不要一开始就删除旧文件，应先让兼容接口指向新实现，测试通过后再清理。

---

## 13. 测试与 Eval 方案

### 13.1 单元测试

- Supervisor 结构化路由解析。
- 不合法 Agent 名称的兜底行为。
- State Reducer 的追加与覆盖语义。
- Knowledge Evidence 序列化和引用编号。
- Tool Registry 权限过滤。
- MCP 超时、重试和错误清洗。
- Reviewer 通过、返工和达到上限后的终止。
- Task Agent 最大步骤限制。

### 13.2 集成测试

- `direct`：无需工具的普通问题。
- `single/knowledge`：仅知识库可回答的问题。
- `single/data`：仅 MCP 工具可回答的问题。
- `parallel`：知识和数据可并行的问题。
- `complex`：存在步骤依赖、需要重规划的问题。
- Knowledge 成功、Data 失败时的部分成功。
- Reviewer 退回一次后通过。
- Reviewer 连续不通过后强制结束。
- 相同 thread 多轮会话与不同 thread 隔离。

### 13.3 API 契约测试

- 新旧请求字段映射。
- SSE 事件都包含 `run_id/type/timestamp/data`。
- 正常完成必须有且只有一个 `run_completed`。
- 异常完成必须有 `run_failed` 或 `run_completed(status=partial)`。
- 客户端断开后停止无必要的后续生成。

### 13.4 Eval 数据集

建议至少建立以下类别，每类先准备 10 条，再逐渐扩充：

| 类别 | 评估重点 |
|---|---|
| 普通对话 | 不滥用 Agent 和工具 |
| 单知识问题 | 路由、召回、引用 |
| 单数据问题 | 工具选择与参数 |
| 多源问题 | 并行调度和聚合 |
| 复杂任务 | 计划质量、完成率、终止 |
| 信息不足 | 是否诚实说明不足 |
| 工具失败 | 是否披露失败并降级 |
| 对抗输入 | 是否越权调用工具 |

### 13.5 建议指标

以下是需要通过 Eval 测得的指标，不是预先写入简历的成绩：

- 路由准确率。
- 必要工具选择准确率与无效工具调用率。
- 知识答案引用覆盖率、引用正确率和 Groundedness。
- 复杂任务完成率。
- 平均 Agent 调度次数、平均工具调用次数、平均 Task 步骤数。
- Reviewer 误拒率、漏检率和返工收益。
- 首 Token 延迟、总延迟、Token 消耗和失败率。
- MCP 降级成功率。

只有形成固定数据集、评估脚本和可复现实验结果后，才能在简历中使用具体百分比。

---

## 14. 安全与治理

第一版即使全部是只读工具，也需要设计以下边界：

- 每个 Agent 使用明确工具白名单。
- Supervisor 不能直接调用底层业务工具。
- MCP 工具默认视为不可信输入，对返回长度和字段做限制。
- 日志中屏蔽 API Key、Cookie、Authorization 和敏感查询参数。
- 上传文件执行扩展名、大小、路径和内容校验。
- 给 Tool Call 设置超时、重试和并发限制。
- 对未来写操作工具增加 Human-in-the-Loop 审批节点。
- 保存可审计的 Agent、工具、参数摘要、结果状态和时间，但不保存模型隐式思维链。

写操作工具不应与只读查询工具使用同一个默认 Tool Registry 配置。

---

## 15. 风险与应对

| 风险 | 表现 | 应对策略 |
|---|---|---|
| 为体现 Multi-Agent 过度拆分 | 每个 Agent 只有一行包装代码 | 以独立目标、Prompt、工具权限、输入输出契约作为 Agent 成立条件 |
| 路由不稳定 | 同一问题反复选择不同 Agent | 结构化输出、有限枚举、规则兜底、路由 Eval |
| 上下文膨胀 | Supervisor 收到全部工具原文 | Specialist 隔离上下文，只返回 AgentResult/Evidence |
| 成本和延迟上升 | 每次都调用 4 个 Agent | direct/single 快路径，Reviewer 只检查复杂或高风险回答 |
| 无限循环 | Reviewer 与 Supervisor 反复返工 | retry_count、max steps、recursion_limit 三重限制 |
| 并行状态覆盖 | AgentResult 丢失 | Reducer 追加、task_id 去重、并发测试 |
| MCP 不稳定 | 整条链路失败 | 工具缓存、Server 级健康状态、部分成功与降级 |
| 重构破坏前端 | SSE 字段不兼容 | 统一事件模型、旧接口适配、契约测试 |
| 简历表述超前 | 面试无法展示 | 每个表述绑定代码、测试、Eval 或演示用例 |

---

## 16. 推荐实施顺序与里程碑

### M1：可测试的 Agent 基础层

包含阶段 0～1。完成后具备测试基线、LLM Factory、Tool Registry、统一运行限制和 MCP 生命周期管理。

### M2：真实 Specialist Agents

包含阶段 2～4。完成后 Knowledge、Data、Task 三个执行单元职责清晰，Task Agent 已去除强运维绑定。

### M3：Multi-Agent 主链路

包含阶段 5～6。完成后 Supervisor 能动态路由，Reviewer 能质量检查并有限返工。这时才可以在简历中写“基于 Supervisor-Specialist 架构实现多智能体协同”。

### M4：产品化交互

包含阶段 7。完成统一 API、SSE 和前端执行时间线，可以稳定演示路由、Agent 执行、工具调用和结果来源。

### M5：工程化与简历证据

包含阶段 8～9。完成持久状态、可观测性、Eval、业务数据和项目文档，最后根据真实评估结果撰写量化简历。

---

## 17. 最小可行版本与完整版边界

### 最小可行版本（优先完成）

- Supervisor Agent。
- Knowledge Agent。
- Data Agent。
- 泛化后的 Task Agent 子图。
- 基于结构化对象的 AgentTask/AgentResult/Evidence。
- direct/single/complex 路由。
- 最大步骤和最大调度次数。
- 新统一流式接口。
- 基础单元测试与 30 条左右 Eval 样例。

### 第二版增强

- Knowledge/Data 并行 fan-out。
- Reviewer 与一次返工。
- 持久化 Checkpointer。
- 统一前端事件时间线。
- Agent 调用和工具调用观测指标。

### 后续可选增强

- 混合检索、Rerank 和查询改写。
- Human-in-the-Loop 审批。
- 动态 Agent Registry。
- 后台异步长任务、取消和恢复。
- 多租户知识库隔离。
- 工具级 Guardrail 与细粒度授权。

---

## 18. 完成定义

只有同时满足以下条件，项目才算完成 Multi-Agent 改造：

1. 至少有两个职责和工具权限不同的 Specialist Agent。
2. Supervisor 能根据请求动态选择 Agent，而不是 API 写死调用链。
3. Agent 之间通过明确契约传递任务和结果。
4. 复杂任务以独立子图运行，并具有终止限制。
5. 能展示 Agent 路由、工具调用、执行步骤和最终来源。
6. 某个 Specialist 失败时系统能够部分成功或明确失败。
7. 存在覆盖路由、工具、状态和循环终止的自动化测试。
8. 存在可重复运行的 Eval 数据和报告。
9. README、代码、演示和简历中的能力表述一致。
10. 核心执行路径使用标准、直接、可读的实现，兜底和兼容代码集中在明确边界，没有无法用故障场景或测试解释的过度防御逻辑。

完成 M3 后，可以使用“多智能体协同架构”的简历描述；完成 M5 后，再加入持久化、评估指标和工程化数据。

---

## 19. 实施后的简历技术主线

改造完成后的项目不再围绕“智能运维”展开，而围绕以下技术主线讲解：

1. 为什么从单 Agent 多工具演进为 Supervisor-Specialist。
2. 如何划分 Knowledge、Data、Task、Reviewer 的职责和工具权限。
3. 如何用 LangGraph 组合顶层 Multi-Agent 图与 Task Agent 子图。
4. 如何通过 AgentTask、AgentResult、Evidence 控制上下文和保证可追踪性。
5. 如何使用 MCP 接入外部工具并处理失败降级。
6. 如何通过 SSE 展示路由、并行任务、步骤和结果。
7. 如何用 Eval 衡量路由、工具选择、Groundedness 和任务完成率。

这条主线能够真实体现 Agent 应用开发中的架构设计、工作流控制、工具治理、状态管理、质量评估和产品交互能力。

---

## 20. 参考架构资料

- LangChain Multi-Agent Subagents：<https://docs.langchain.com/oss/python/langchain/multi-agent/subagents>
- LangChain Multi-Agent Router：<https://docs.langchain.com/oss/python/langchain/multi-agent/router>
- LangChain Multi-Agent Handoffs：<https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs>
- LangChain Custom Workflow：<https://docs.langchain.com/oss/python/langchain/multi-agent/custom-workflow>
- Anthropic Building Effective Agents：<https://www.anthropic.com/engineering/building-effective-agents>
- Model Context Protocol Architecture：<https://modelcontextprotocol.io/specification/2025-06-18/architecture>
