# Oncall_agent 简历撰写交接

## 下一轮目标

基于当前已经完成并验收的项目，为 AI 应用开发、Agent 工程或 Python 后端方向撰写真实、可解释、可复现的简历项目描述。不要继续扩展项目范围，除非发现简历主张缺少必要证据。

## 工作区状态

- 仓库：`/Users/heng/Desktop/master/agent_project/Oncall_agent`
- 分支：`multi-agents`
- M1～M5（主计划阶段 0～9）已按当前约定范围完成。
- 工作树包含尚未提交的 M1～M5 及后续前端可读性修复；不得丢弃、覆盖或假定这些修改已经提交。
- 用户已解决 VSCode/Python 环境问题；下一轮无需继续排查该问题。
- 本轮未读取、复制或记录 `.env` 内容。

开始下一轮前先读取仓库根目录 `AGENTS.md`（用户在会话中提供的规则同样适用），并执行 `git branch --show-current`、`git status --short`。

## 当前项目结论

项目已经从 RAG 对话与 AIOps 演示，演进为本地单实例可运行的企业知识服务多智能体协同平台。当前具备 Supervisor-Specialist 顶层图、Knowledge/Data/Task/Reviewer、通用 Task 子图、证据与引用、只读 MCP、统一 JSON/SSE、SQLite 重启恢复、run trace、离线契约 Eval、业务知识资料和业务工单 MCP。

完整实现与限制不要在本文件重复展开，以以下文件为事实来源：

- 路线图与阶段边界：`xzh_docs/plan_docs/multi-agent-refactor-plan.md`
- 最终工程交接与验收：`xzh_docs/handoff_docs/2026-08-23-m5-implementation-handoff.md`
- 架构：`xzh_docs/architecture/multi-agent-architecture.md`
- 执行时序：`xzh_docs/architecture/execution-sequence.md`
- 演示场景：`xzh_docs/demo/m5-demo-scenarios.md`
- Eval 口径：`xzh_docs/eval_reports/README.md`
- 验收快照：`xzh_docs/eval_reports/m5_acceptance.json`
- 当前简历草稿：`xzh_docs/resume/多智能体协同平台.md`
- 项目入口与可运行命令：`README.md`

## 本轮最后补充的修复

用户在网页测试复杂任务时发现结果难以阅读。已完成：

- 执行过程改为可折叠纵向时间线。
- 修复 Task 子图工具事件被嵌套后 `tool_name` 在前端为空的问题。
- 最终结果按状态、路由、参与 Agent、正文、错误和来源分区。
- 长 Pydantic 参数错误在页面中转成简短中文提示，详细错误折叠。
- 来源卡片默认折叠并显示来源类型。
- 多 Agent 汇总时，将各 Agent 的局部 `[来源N]` 重映射到最终全局来源编号。

对应实现：`static/app.js`、`static/styles.css`、`app/agent/graph.py`；对应回归测试：`tests/contract/test_frontend_agent_contract.py`、`tests/integration/test_multi_agent_graph.py`。

当前环境没有可连接的浏览器实例，所以没有截图级视觉验收；完成了 JavaScript 语法检查和前端静态契约测试。不要将其写成已完成浏览器视觉回归。

## 最新验收事实

- `ONCALL_DISABLE_ENV_FILE=1 make test-quick`：98 项测试通过。
- `ONCALL_DISABLE_ENV_FILE=1 make lint`：通过。
- `ONCALL_DISABLE_ENV_FILE=1 make type-check`：80 个源码文件通过。
- `node --check static/app.js`：通过。
- `make eval`：固定离线契约数据集 34/34；M4 基线和 M5 当前分数均为 1.0，使用相同数据集，差值 0。
- SQLite Checkpointer 重开恢复、Service 实例重建后的 thread/trace 恢复、SQLite-backed SSE 单终态均有自动化测试。
- Business Ticket MCP 完成过本地协议级工具发现和实际只读调用。
- `bandit` 未执行，因为当前虚拟环境没有安装该可选工具。

测试覆盖率和 34/34 只能描述为测试/Eval 契约通过情况，不能当作线上回答准确率或模型质量指标。

## 简历表述边界

可以写：

- Supervisor-Specialist 多智能体编排及 direct/single/parallel/complex 路由。
- 类型化 AgentTask、AgentResult、Evidence、RouteDecision、ReviewResult 契约。
- Knowledge Agent 的 Milvus RAG、标准化证据和引用。
- Data Agent 的只读工具白名单、MCP 发现缓存、超时、有限重试和失败降级。
- Task Agent 的 Plan-Execute-Replan 子图及硬终止限制。
- Reviewer 结构化审核和最多一次返工。
- 统一 FastAPI JSON/SSE、单一终态、旧 API 渐进兼容。
- AsyncSqliteSaver、本地单实例重启恢复、run_id Trace 和运行指标。
- 98 项自动化测试与 34 条固定离线契约 Eval。

不得写：

- 生产级分布式系统、生产可用、万级并发或经过线上流量验证。
- 回答准确率 100%、检索召回率、Groundedness 或任何没有真实评估集支持的模型质量数字。
- Token 成本优化或完整 usage 统计；当前相关指标在没有可靠数据时为 `null`。
- 长期记忆、跨多实例共享状态、Task 子图任意中间步骤断点续跑。
- 已完成浏览器视觉回归或性能压测。

建议将 SQLite 描述为“本地单实例状态恢复”，不要主动包装为分布式架构。

## 下一位 Agent 的建议步骤

1. 阅读上述事实来源，重点核对 M5 交接、Eval 口径和现有简历草稿。
2. 先询问用户目标岗位、工作年限、期望篇幅及是否需要 STAR/项目介绍/面试讲解多个版本；若上下文已明确则直接工作。
3. 形成一版 4～6 条的简历项目要点，每条都标明可追溯的代码或测试证据。
4. 区分“架构亮点”“工程可靠性”“可复现验收”，避免堆砌框架名。
5. 如果引用数字，只使用 98 项测试和 34 条离线契约 Eval，并紧邻说明其口径。
6. 对现有 `xzh_docs/resume/多智能体协同平台.md` 做真实性审查后再修改；用户未要求时不要创建提交、PR 或改业务代码。

## Suggested skills

- `documents:documents`：仅在用户要求输出正式 `.docx` 简历时使用，并遵循其渲染验收流程。
- `browser:control-in-app-browser`：仅在用户要求重新演示网页或补视觉证据且浏览器可用时使用。
- 当前没有专门的简历写作技能；若只修改 Markdown 简历，直接基于仓库事实和上述证据完成，不需要额外技能。

