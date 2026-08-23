# M3 Multi-Agent 主链路实施交接

> 完成时间：2026-08-23 16:31 CST
> 分支：`multi-agents`
> 范围：阶段 5（Supervisor 与顶层图）和阶段 6（Reviewer 质量闭环）

## 1. 完成内容

- 扩展公共契约，新增有限枚举的 `RouteDecision`、结构化 `ReviewResult`、
  `DispatchRecord` 和 `MultiAgentRunResult`；非法路由形状在进入图前由 Pydantic 拒绝。
- 实现独立 Supervisor Agent：只负责 direct/single/parallel/complex 路由、任务委派、
  直接回答和审核返工决策，不持有 Milvus、MCP 或本地业务工具。
- Supervisor 使用结构化 LLM 路由；模型失败或输出非法时使用有限的确定性规则降级，
  路由依据只保存可展示摘要，不保存隐藏思维链。
- 新增顶层 LangGraph，显式连接 Supervisor、Knowledge、Data、Task、Aggregate、Reviewer、
  Retry 和 Finalize 节点。Knowledge 与 Data 通过 `Send` 在无依赖场景并行扇出。
- 顶层状态区分 `user_id`、`thread_id` 和 `run_id`。Agent 结果、调度记录、审核历史和
  工作流错误使用追加 Reducer；路由、待执行任务和候选答案使用覆盖语义。
- 每个 Specialist 调用具有超时限制，异常被清洗并转换成失败结果；调度记录包含轮次、
  Agent、任务、状态和耗时。并行时单个 Specialist 失败不会丢弃其他成功结果。
- 结果聚合按任务 ID 去重，保留成功、部分成功和失败事实，并在候选答案中显式披露
  Agent 或工具错误。
- 实现无工具 Reviewer Agent：只接收原始请求、候选答案、证据和已记录错误，返回
  结构化审核结果，不能直接修改答案或补造工具事实。
- Reviewer 在语义审核前执行确定性检查，可识别无证据数字、无效知识引用和遗漏错误
  披露；复杂、并行和非完整结果进入审核，成功的 direct/single 请求保留快速路径。
- Reviewer 只能定向返工一次。返工后无论再次通过或拒绝都进入终态；配置层也强制
  `reviewer_max_retries` 只能为 0 或 1。
- 图设置 Supervisor 轮次、Agent 超时、Reviewer 返工和递归硬限制，所有公开运行结果
  都进入 `completed`、`partial` 或 `failed`，图级异常也转换为清洗后的失败终态。
- 使用 `thread_id:run_id` 作为开发 Checkpoint 内部键，避免同一 thread 的不同 run
  互相混入执行结果。

## 2. 验收结果

以下命令于 2026-08-23 在 Python 3.13.9 环境通过。测试显式禁用 `.env` 并清空
`DASHSCOPE_API_KEY`，未访问真实 LLM、Milvus 或在线 MCP Server：

```text
ONCALL_DISABLE_ENV_FILE=1 DASHSCOPE_API_KEY='' make check-all
  -> format、Ruff、73 tests 全部通过

ONCALL_DISABLE_ENV_FILE=1 DASHSCOPE_API_KEY='' make type-check
  -> Success: no issues found in 73 source files
```

M3 自动化验收覆盖：

- direct、single/knowledge、single/data、parallel 和 complex 五条顶层路由路径。
- Knowledge 与 Data 同时启动的真实异步并行行为，以及并行结果的追加 Reducer。
- Specialist 失败、Agent 超时、部分成功、错误披露和图级异常清洗。
- Supervisor 非法/失败路由的有限确定性降级，以及 Reviewer 定向返工任务。
- Reviewer 通过、无证据数字、无效引用、遗漏错误、返工一次后通过和连续拒绝终止。
- 相同 `thread_id` 下不同 `run_id` 的执行结果隔离。
- Supervisor 与 Reviewer 的底层工具权限均为空。

测试覆盖率输出仅作为本地基线观测值，不作为质量成绩、生产指标或简历量化指标。

## 3. 当前能力与限制

- 当前代码已经形成 Supervisor-Specialist 顶层主链路，Knowledge、Data、Task 和 Reviewer
  具有独立职责、提示词、权限与结构化边界，可以表述为已实现多智能体协同架构。
- M3 提供的是内部 Python/LangGraph 运行入口。统一 `/api/agent/run`、
  `/api/agent/run_stream`、SSE 信封和前端执行时间线属于 M4，尚未实现。
- 旧 `/api/chat`、`/api/chat_stream` 和 `/api/aiops` 尚未改接顶层图，仍保持 M1/M2
  兼容行为；因此当前浏览器入口还不能自动展示 Supervisor 路由。
- `MemorySaver` 仍是单进程开发环境内存状态，不是持久化存储，也不提供跨进程会话恢复。
- Reviewer 的确定性规则和 LLM 审核具备行为测试，但误拒率、漏检率和返工收益尚无固定
  Eval 数据集结果，不声明相关量化指标。
- 调度耗时是单次运行记录，不构成性能或并发能力证明。

## 4. 下一阶段

按主计划进入 M4：增加统一 Agent JSON/SSE API、事件信封、旧接口兼容适配和前端执行
时间线。实施时应保证每个流只有一个终态事件，并同步增加新旧 API 契约测试和客户端
断开行为测试。
