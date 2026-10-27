# AGENTS.md

## 1. 本文件的作用

本文件适用于整个仓库，供每次新会话快速建立代码地图、确认真实能力并选择正确的修改入口。
若子目录存在更深层级的 `AGENTS.md`，其规则只在对应子树中补充或覆盖本文件。

开始工作前必须：

1. 运行 `git branch --show-current`，确认当前分支。Multi-Agent 改造统一在
   `multi-agents` 分支进行。
2. 运行 `git status --short`，识别用户已有修改和未跟踪文件；不得覆盖、清理或回退它们。
3. 先读目标模块的当前实现和相邻测试，再修改。规划文档不能代替源码。
4. 查找目标目录下是否还有更深层级的 `AGENTS.md`。

除非用户明确要求，不得创建、切换、合并、变基或删除分支，也不得提交、推送或创建
Pull Request。

## 2. 一分钟项目地图

这是一个基于 FastAPI、LangGraph、Milvus 和 MCP 的企业知识服务 Multi-Agent 项目。
当前主链路已经实现 Supervisor-Specialist 编排，并保留旧 Chat/AIOps 接口作为兼容层。

主请求链路：

```text
HTTP / SSE 请求
  -> app/main.py
  -> app/api/agent.py
  -> app/services/multi_agent_service.py
  -> app/agent/graph.py（顶层 LangGraph）
       -> Supervisor：直接回答，或生成受约束的路由
       -> Knowledge Agent：Milvus 检索、证据化回答
       -> Data Agent：调用白名单内的只读 MCP / 本地工具
       -> Task Agent：Plan -> Execute -> Replan 子图
            -> 可按步骤委派 Knowledge 或 Data
       -> Aggregate：合并结果、统一引用编号
       -> Reviewer：按需检查并最多触发一次返工
       -> Final Response：生成用户可见答案
  -> MultiAgentService：标准化响应、SSE、Run/Trace/Metrics、工作记忆更新
  -> RunStore + LangGraph Checkpointer
```

文档写入与检索链路：

```text
/api/upload 或 /api/index_directory
  -> app/api/file.py
  -> vector_index_service.py
  -> document_splitter_service.py
  -> vector_store_manager.py
  -> Milvus

Knowledge Agent
  -> MilvusKnowledgeRetriever
  -> knowledge_tool.py / vector_search_service.py
  -> Evidence
  -> 带 [来源N] 的回答
```

应用生命周期：

- `app/main.py` 的 FastAPI lifespan 启动 `MultiAgentService`，连接 Milvus，并预热 MCP 工具。
- `MultiAgentService.startup()` 通过 `PersistenceFactory` 成对创建 Checkpointer、RunStore，
  PostgreSQL 模式还会装配 WorkingMemoryStore。
- MCP 预热失败只降级对应外部工具，不应阻止无关的知识能力启动。
- PostgreSQL 是默认持久化后端；数据库或 Schema 未就绪时明确启动失败，不静默回退。

## 3. 当前真实能力与边界

判断能力时以当前源码和测试为准。当前已实现：

- 顶层 Supervisor-Specialist LangGraph。
- Knowledge、Data、Task、Reviewer 四类职责独立的 Agent。
- `direct`、`single`、`parallel`、`complex` 四种受类型约束的路由模式。
- 只对不同的只读 Specialist 使用并行扇出；复杂依赖任务进入 Task Agent 顺序执行。
- Task Agent 内部的 Planner、Executor、Replanner 是一个复合 Agent 的节点，不是三个 Agent。
- 统一 JSON/SSE Agent API，以及旧 Chat/AIOps 兼容接口。
- 统一事件信封、公开 Run/Trace/Metrics 存储和线程历史查询。
- `memory`、`sqlite`、`postgres` 三种 Checkpointer/RunStore 后端。
- PostgreSQL 下的有界跨轮工作记忆：证据引用、动态证据 TTL、CAS 更新和可选压缩。
- 固定离线 Eval 数据集与比较脚本。

不得夸大为以下能力：

- `MemorySaver` 只是进程内开发状态；SQLite 是本地持久化；只有配置并初始化后的 PostgreSQL
  才是共享持久化后端。
- 工作记忆是有界、结构化的案件上下文快照，不是无限长期记忆，也不保存隐藏思维链。
- Metrics 中的 token 与成本字段当前可以为空；不得据此声称完整成本追踪。
- Eval 结果仅代表已提交数据集和执行环境，不得外推“生产可用”“万级并发”或未复现的准确率。
- MCP Server 和 MCP Tool 是外部能力，不是 Agent；工作流节点数量也不等于 Agent 数量。

## 4. 目录与模块职责

```text
Oncall_agent/
├── app/
│   ├── main.py                    FastAPI 入口、lifespan、路由和静态文件
│   ├── config.py                  唯一集中配置入口（Pydantic Settings）
│   ├── api/                       HTTP/SSE 边界与兼容适配
│   ├── models/                    公共 API 请求、响应与事件模型
│   ├── agent/
│   │   ├── graph.py               顶层 Supervisor-Specialist LangGraph
│   │   ├── state.py               顶层 TypedDict 状态和 Reducer 语义
│   │   ├── contracts.py           AgentTask/Result/Evidence/Route/Review 等契约
│   │   ├── final_response.py      最终自然语言回答生成
│   │   ├── supervisor/            Supervisor 路由策略、Prompt、Schema
│   │   ├── specialists/
│   │   │   ├── knowledge/         Milvus 检索、证据标准化、有依据回答
│   │   │   ├── data/              只读工具规划、调用、脱敏、动态证据
│   │   │   ├── task/              Plan-Execute-Replan 子图及步骤委派
│   │   │   └── reviewer/          确定性检查 + 结构化 LLM 复核
│   │   ├── memory/                跨轮工作记忆契约、上下文、Reducer、TTL、压缩
│   │   ├── runtime/               Checkpointer 工厂和运行硬限制
│   │   ├── mcp_client.py          MCP 客户端、重试、错误清洗与降级
│   │   └── aiops/                 旧导入路径兼容层
│   ├── services/
│   │   ├── multi_agent_service.py 统一执行、SSE、公开模型、持久化与工作记忆协调
│   │   ├── event_service.py       安全事件信封、大小限制与脱敏
│   │   ├── persistence.py         同一 lifespan 下组装持久化资源
│   │   ├── run_store.py           Memory/SQLite/PostgreSQL RunStore
│   │   ├── working_memory_store.py PostgreSQL 工作记忆存储
│   │   ├── aiops_service.py       旧 AIOps 请求到 Task Agent 的适配
│   │   ├── rag_agent_service.py   旧 RAG Agent 实现，兼容期保留
│   │   └── vector_*.py            切分、Embedding、索引、存储和检索服务
│   ├── tools/
│   │   ├── registry.py            中央工具注册表和 Agent 级白名单
│   │   ├── mcp_provider.py        MCP 工具发现、缓存、来源与健康状态
│   │   ├── knowledge_tool.py      知识库检索工具
│   │   ├── query_metrics_alerts.py 本地只读告警查询工具
│   │   └── time_tool.py           本地时间工具
│   ├── core/                      LLM Factory 与 Milvus 客户端
│   ├── evaluation/runner.py       离线 Eval 执行与基线比较
│   └── cli/                       数据库升级与 SQLite RunStore 迁移命令
├── tests/
│   ├── unit/                      契约、路由、Agent、工具、记忆等纯单元测试
│   ├── integration/               图协作、降级、持久化和隔离测试
│   ├── contract/                  JSON、SSE、前端和旧接口兼容测试
│   ├── evals/                     固定数据集与离线评估
│   └── fakes/                     外部边界的测试替身
├── mcp_servers/                   CLS、Monitor、Ticket 三个本地 MCP 示例服务
├── migrations/                    Alembic 业务表迁移
├── static/                        原生 HTML/CSS/JS 前端
├── business-docs/                 产品、规则、FAQ、交付知识库样例
├── aiops-docs/                    运维故障知识库样例
├── xzh_docs/
│   ├── architecture/              当前架构与执行时序
│   ├── plan_docs/                 路线图和实施计划，可能包含未实现能力
│   ├── handoff_docs/              所有任务交接文档的唯一目录
│   ├── eval_reports/              已生成并提交的评估报告
│   └── appendices/、labs/ 等      教程、参考和实验文档
├── Makefile                       本地开发、测试、服务和数据库命令
├── pyproject.toml                 Python 版本、依赖、测试和格式规则
├── vector-database.yml            Milvus Docker Compose
└── postgres-database.yml          PostgreSQL Docker Compose
```

## 5. 核心文件如何协作

### 5.1 顶层编排

`app/agent/graph.py` 的固定节点是：

```text
START -> supervisor
  direct ---------------------------> aggregate
  single/parallel/complex -> specialist(s) -> aggregate
aggregate -> fast path -> final_response -> END
aggregate -> reviewer
reviewer -> final_response -> END
reviewer -> prepare_retry -> supervisor   （受限返工）
```

关键不变量：

- Supervisor 只能路由到 `knowledge`、`data`、`task`。
- `RouteDecision` 在 `app/agent/contracts.py` 校验路由形状；不得执行模型任意生成的名称。
- `parallel` 只能包含不同的 Knowledge/Data 只读任务；`complex` 只能进入 Task Agent。
- Specialist 并行结果通过 `operator.add` Reducer 追加；路由和候选答案按节点覆盖。
- 每个 run 的 checkpoint thread key 为 `thread_id:run_id`，避免同一业务线程内不同运行互相污染。
- 所有路径必须进入 `completed`、`partial` 或 `failed`，并带 `termination_reason`。
- Supervisor 轮次、Reviewer 返工、Task 步骤、图递归、Agent 超时、工具超时和重试均有硬限制。

### 5.2 Agent 权限边界

| Agent | 职责 | 可用能力 | 不应持有 |
|---|---|---|---|
| Supervisor | 路由、任务拆分、返工决策 | 结构化 LLM 路由、已有结果与记忆视图 | Milvus、MCP、底层业务工具 |
| Knowledge | 内部知识问答 | Milvus Retriever、静态证据 | Data/MCP 工具 |
| Data | 当前数据查询 | Registry 分配的只读本地/MCP 工具 | Knowledge Retriever、写入工具 |
| Task | 有依赖的多步骤任务 | Planner、Executor、Replanner；逐步委派 Knowledge/Data | 无界循环、无约束并行 |
| Reviewer | 完整性、证据和错误披露检查 | 原请求、候选答案、已有证据和错误 | 底层工具、新证据生成 |

一个新增模块只有同时具备独立目标、自己的指令策略、明确权限、结构化输入输出和独立行为测试，
才能命名为 Agent。普通函数、服务、工具和 LangGraph 节点不要命名为 Agent。

### 5.3 公共契约

- Agent 间契约：`app/agent/contracts.py`。
- 顶层图状态：`app/agent/state.py`；Task 子图状态：`app/agent/specialists/task/state.py`。
- 公共 API/SSE 契约：`app/models/agent.py`。
- 工作记忆契约：`app/agent/memory/contracts.py`。
- API 与 Agent 边界使用 Pydantic；LangGraph 状态使用 `TypedDict`。
- Pydantic 可变默认值必须使用 `Field(default_factory=...)`。
- 原始模型响应、原始 MCP 返回和内部状态不得直接进入公共 API。

### 5.4 持久化的三个概念

不要混淆：

1. **LangGraph Checkpointer**：保存图执行状态，由 `runtime/checkpointer.py` 创建。
2. **RunStore**：保存公开的 run、event、trace、metrics 和线程历史。
3. **WorkingMemoryStore**：保存每个业务 `thread_id` 的结构化跨轮上下文快照。

`PersistenceFactory` 确保前两者选择同一后端。当前后端语义：

| 后端 | Checkpoint | Run/Trace | Working Memory | 用途 |
|---|---|---|---|---|
| `memory` | 进程内 | 进程内 | 无 | 单元测试、短期开发 |
| `sqlite` | 本地文件 | 同一本地文件 | 无 | 单机本地回退 |
| `postgres` | PostgreSQL | PostgreSQL | 可启用 | 默认共享持久化 |

PostgreSQL Schema 由两部分组成：Alembic 管理 `agent_runs`、`agent_events`、
`agent_working_memories`；LangGraph saver 自己初始化 checkpoint 表。统一使用
`python -m app.cli.database upgrade` 或 `make db-upgrade`。

## 6. API 与前端契约

统一接口位于 `app/api/agent.py`：

| 方法与路径 | 作用 |
|---|---|
| `POST /api/agent/run` | 同步执行，返回 `AgentRunResponse` |
| `POST /api/agent/run_stream` | SSE 执行，返回统一事件信封 |
| `GET /api/agent/runs/{run_id}` | 查询公开运行结果 |
| `GET /api/agent/runs/{run_id}/trace` | 查询事件和指标 |
| `GET /api/agent/metrics` | 查询聚合运行指标 |
| `GET /api/agent/threads/{thread_id}` | 查询公开线程历史 |
| `DELETE /api/agent/threads/{thread_id}` | 删除该线程的 Run、Checkpoint 和工作记忆 |

兼容接口：

- `POST /api/chat`、`POST /api/chat_stream`、`POST /api/chat/clear`、
  `GET /api/chat/session/{session_id}` 适配到统一 Multi-Agent 服务。
- `POST /api/aiops` 已标记 deprecated，通过 `AIOpsService` 适配 Task Agent。
- `/api/upload` 与 `/api/index_directory` 负责知识库写入。

修改 API 或 SSE 时必须：

- 先更新 Pydantic 模型，再更新服务与路由。
- 统一事件至少保留 `event_id`、`run_id`、`thread_id`、`type`、`timestamp` 和 `data`。
- 每个 SSE 流只发送一个终态事件：`run_completed` 或 `run_failed`。
- 同一次修改更新 `static/app.js`、API 文档和新旧接口契约测试。
- 不向浏览器暴露 Prompt、隐藏推理、凭据、Authorization Header、原始异常栈或无限制工具结果。

## 7. 常见修改应从哪里开始

| 修改目标 | 首要文件 | 同时检查 |
|---|---|---|
| 路由策略或并行规则 | `agent/supervisor/agent.py`、`agent/contracts.py` | `agent/graph.py`、Supervisor/图测试 |
| 顶层节点或循环 | `agent/graph.py`、`agent/state.py` | `runtime/limits.py`、集成测试 |
| 知识检索与引用 | `specialists/knowledge/` | `knowledge_tool.py`、vector 服务、固定 Eval |
| MCP/Data 工具 | `tools/registry.py`、`tools/mcp_provider.py`、`specialists/data/` | MCP 降级、脱敏、重名和权限测试 |
| 复杂任务执行 | `specialists/task/` | `aiops_service.py`、Task 集成测试 |
| Reviewer 规则 | `specialists/reviewer/` | 顶层返工路径、Reviewer 单元测试 |
| 工作记忆 | `agent/memory/`、`working_memory_store.py` | `multi_agent_service.py`、迁移、记忆测试 |
| JSON/SSE 协议 | `models/agent.py`、`api/agent.py`、`event_service.py` | `static/app.js`、contract 测试 |
| 持久化后端 | `persistence.py`、`run_store.py`、`runtime/checkpointer.py` | CLI、migrations、持久化测试 |
| LLM 模型选择 | `core/llm_factory.py` | `config.py`，不要在节点中直接实例化 |
| 文档上传与检索 | `api/file.py`、`services/vector_*` | Milvus、文件安全、RAG 回归 |

修改前推荐阅读顺序：

1. `app/main.py`
2. `app/api/agent.py` 与 `app/models/agent.py`
3. `app/services/multi_agent_service.py`
4. `app/agent/contracts.py`、`state.py`、`graph.py`
5. 对应的 Supervisor 或 Specialist
6. 对应测试
7. `xzh_docs/architecture/` 中的当前架构说明

`xzh_docs/plan_docs/multi-agent-refactor-plan.md` 是路线图；其中目标结构和未来能力可能已过期或尚未
实现。交接当前进行中的工作时，再查看 `xzh_docs/handoff_docs/` 中日期最新且主题匹配的文档。

## 8. 开发规范

- Python 支持版本以 `pyproject.toml` 为准：当前为 3.11 至 3.13。
- 遵循 Black/Ruff 配置，行宽 100；新增公共函数和模型必须有类型标注。
- LLM、MCP、网络和图执行路径优先使用 `async def`，不得用同步网络请求阻塞事件循环。
- 复用 `app/core/llm_factory.py`；模型实例、模型名、URL、超时和凭据均从 `app/config.py` 获取。
- 避免 import 阶段的网络连接和昂贵初始化，使用 FastAPI lifespan 或显式延迟初始化。
- 使用 Loguru。适用时记录 `run_id`、`thread_id`、Agent、节点/工具、耗时和状态。
- 正常流程保持直接清晰；只在 LLM、MCP、Milvus、网络、文件和数据库等真实系统边界捕获异常。
- 只有能恢复时才捕获异常；失败必须通过日志、结构化错误或终态保持可见。
- 不添加宽泛静默 `except`、伪成功、无意义包装层或只为“以后可能复用”的抽象。
- 规则明确时优先确定性代码；语义判断才交给结构化输出的 LLM。

## 9. RAG、工具与记忆约束

- 上传、切分、索引、检索、AgentResult 和最终引用必须保留来源元数据。
- Knowledge Agent 有检索结果时必须返回 Evidence；证据不足要明确说明。
- 检索失败与检索为空是不同状态，不得合并成静默空结果。
- 修改 Chunk、Top-K、相关性阈值、查询改写、Rerank 或引用规则时，运行固定数据集回归。
- 工具统一经 Registry/Provider 管理并按 Agent 白名单分配；检测重名并保留 MCP Server 来源。
- 工具默认只读。新增写入或破坏性工具必须设计显式授权和 Human-in-the-Loop 审批节点。
- MCP 发现结果按配置缓存，不得在每个图节点重复发现全部工具。
- 文档、MCP 响应和工具输出全部视为不可信输入，进入模型和 API 前做长度限制和脱敏。
- 动态证据必须包含带时区的 `observed_at`，由服务端策略设置 `valid_until`；过期事实必须重查。
- 工作记忆只能保存公开请求、受支持证据、错误摘要和业务状态；禁止保存隐藏思维链。

## 10. 测试与验证

测试不得默认依赖真实 DashScope Key、真实 Milvus 或在线 MCP。使用 Fake 或依赖注入隔离外部边界。
`tests/conftest.py` 会禁止测试读取开发者本地 `.env`。

测试分层：

- `tests/unit/`：契约、Reducer、路由、权限、证据、脱敏、限制和 Agent 行为。
- `tests/integration/`：Specialist 协作、部分失败、MCP 降级、Checkpoint 隔离和持久化。
- `tests/contract/`：统一 JSON/SSE、前端消费和旧 API 兼容。
- `tests/evals/`：固定请求集上的路由、证据、工具选择和 Reviewer 行为。
- PostgreSQL 实例测试带 `postgres` marker，需要独立测试数据库。

常用命令：

```bash
make test-quick   # 当前仍运行整个 tests/，输出比 make test 简略
make test         # 全部 pytest，并显式生成覆盖率报告
make lint         # Ruff 检查 app/ 与 tests/
make type-check   # 当前只运行 mypy app/
make security     # Bandit 检查 app/
make eval         # 固定离线评估
make check-all    # 依次执行 format、lint、test；format 会修改 app/ 源码
```

开发时先运行最小相关测试；行为变更必须补相应测试。较大修改完成前运行所有可行检查，并报告实际
运行项、结果和环境限制。需要只读验证时，不要直接使用会改写源码的 `make format` 或
`make check-all`。仅文档修改可做针对性的内容与 diff 校验，无需为此运行完整应用测试。

## 11. 数据库与本地运行

常用入口：

```bash
make install-dev
make postgres-up
make db-upgrade
make up            # Milvus
make start         # MCP + FastAPI 后台服务
make dev           # 前台热重载
make stop
```

配置示例只从 `.env.example` 读取。不得读取、打印、复制、总结或提交真实 `.env`。
默认 PostgreSQL 配置需要有效 `DATABASE_URL`；不得在代码中硬编码部署凭据。

## 12. 安全、Git 与生成文件

- 禁止提交 API Key、Token、Cookie、账号凭据、私有端点和用户数据。
- 如果受 Git 跟踪的内容或命令输出出现凭据，停止传播并提示用户轮换。
- 上传入口必须校验文件名、大小、扩展名和解析后的真实路径。
- 不记录完整文档、无限制工具输出、凭据或个人数据。
- 保留 `.gitignore` 对 `.env`、日志、PID、上传文件、数据库、覆盖率、数据集、Artifacts 和
  Checkpoint 的忽略规则。
- 源码和文档的明确修改使用 `apply_patch`。
- 不使用 `git reset --hard`、`git checkout --` 等破坏性命令，不重写历史。
- 较大修改前后都检查 `git status --short`，最终状态只能包含本任务修改和用户已有工作。

## 13. 文档真实性与交接

- 所有任务交接文档只放在 `xzh_docs/handoff_docs/`；不要散落到仓库根目录或其他文档目录。
- 重要契约、依赖方向或运行方式变化时，同步更新 `xzh_docs/architecture/`、README 和 API 文档。
- README 中的命令和接口必须与当前实现一致。
- 计划能力必须标记为“计划中”；已实现能力必须能对应到源码、测试或可复现演示。
- 不得虚构准确率、召回率、Groundedness、延迟、并发量或成本数据。
- 简历与项目介绍中的每项能力都应能指向已提交代码、测试、数据集或评估结果。

## 14. 完成前检查

按本次任务适用范围确认：

- 修改遵守 Agent 职责和工具权限边界。
- 图和工具仍有硬限制，所有路径具有明确终态。
- Reducer 的追加/覆盖语义正确，并发 run 和 thread 仍隔离。
- 错误、证据不足与部分成功被真实表达。
- API/SSE 与前端、旧兼容接口同步。
- 持久化 Schema、服务生命周期和删除语义保持一致。
- 测试覆盖行为变化，已运行足够的验证。
- 文档只描述当前实现，没有新增密钥或生成态文件。
- `git diff -- AGENTS.md` 和 `git status --short` 符合预期。
