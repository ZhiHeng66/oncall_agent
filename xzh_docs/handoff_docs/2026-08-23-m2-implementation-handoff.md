# M2 Specialist 与通用任务子图实施交接

> 完成时间：2026-08-23 16:14 CST  
> 分支：`multi-agents`  
> 范围：阶段 2（Knowledge Agent）、阶段 3（Data Agent）与阶段 4（Task Agent）

## 1. 完成内容

- 新增跨 Agent 公共契约 `AgentTask`、`AgentResult`、`Evidence` 和
  `TaskStepResult`，统一表达任务、状态、证据、错误与步骤结果。
- 实现独立 Knowledge Agent：通过 Milvus Retriever 获取文档，保留来源元数据，
  输出标准化证据和引用，并区分检索失败、检索为空、证据相关度不足和回答生成失败。
- 实现独立 Data Agent：只允许调用 Registry 分配的只读 Data 工具，限制单次任务的
  工具调用数和超时，保留 MCP Server/Tool 来源，并对错误和工具结果进行脱敏、截断。
- 收紧 Tool Registry 权限：Knowledge、Data、Task、Supervisor、Reviewer 和旧 Chat
  使用独立权限域；Task Agent 不持有底层工具，只能委派 Specialist。
- 将原 AIOps Plan-Execute-Replan 泛化为 Task Agent LangGraph 子图，使用通用提示词、
  类型化状态和显式 Plan、Execute、Replan 节点。
- Task Agent 为步骤数、重规划和图递归设置硬限制，所有执行路径进入 `completed`、
  `partial` 或 `failed` 终态；生命周期事件不包含隐藏推理。
- 旧 AIOps Service 和 `app/agent/aiops` 保留为兼容适配层，继续输出旧接口预期的
  事件形状，迁移期间未删除旧路由。
- 将 RAG 模型和 Embedding 客户端改为延迟创建，测试导入阶段不读取模型凭据或访问
  外部服务；移除 Embedding 初始化时输出部分 API Key 的旧日志行为。

## 2. 验收结果

以下命令均于 2026-08-23 在本地环境通过，且测试显式禁用 `.env` 并清空
`DASHSCOPE_API_KEY`：

```text
ONCALL_DISABLE_ENV_FILE=1 DASHSCOPE_API_KEY='' make check-all
  -> format、Ruff、46 tests 全部通过

ONCALL_DISABLE_ENV_FILE=1 DASHSCOPE_API_KEY='' make type-check
  -> Success: no issues found in 63 source files
```

测试覆盖以下关键验收场景：

- Knowledge Agent 的证据、引用、元数据、空结果、低相关度、检索失败与生成失败。
- Data Agent 的工具白名单、只读限制、调用上限、超时、部分失败、MCP 来源和脱敏。
- Task Agent 的三个非 AIOps 任务类别、事件生命周期、步骤上限、部分失败、异常终止、
  空计划和 Replanner 失败。
- 旧 AIOps 适配器的事件形状和单一终态兼容。

测试覆盖率输出仅作为当前基线观测值，不作为质量成绩或简历量化指标。

## 3. 兼容性与当前限制

- `/api/chat`、`/api/chat_stream` 和 `/api/aiops` 路径未删除，旧 AIOps 事件协议保留。
- 当前已经具备边界独立的 Knowledge、Data 和 Task Agent，但尚未实现 Supervisor、
  Reviewer 与顶层协同图，因此不能表述为完整的多智能体主链路。
- 统一 `/api/agent/run`、`/api/agent/run_stream`、统一 SSE 信封和前端迁移尚未实施。
- `MemorySaver` 仍仅是单进程开发环境内存状态，不是持久化存储。
- Knowledge Agent 的实际检索质量仍需固定 Eval 数据集验证；当前不声明准确率、召回率
  或 Groundedness 指标。
- 检查中发现旧 Embedding 初始化日志曾输出部分掩码后的真实凭据。代码路径已经移除，
  但该凭据应视为已经暴露并完成轮换。

## 4. 下一阶段

按主计划进入 M3：实现 Supervisor、顶层 LangGraph、结果聚合与 Reviewer 有限返工。
开始前应先固定 RouteDecision、ReviewResult、全局状态 Reducer、调度轮次上限和终态
验收矩阵；Supervisor 与 Reviewer 继续保持零底层工具权限。
