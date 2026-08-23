# Multi-Agent 二次开发交接文档

> 交接时间：2026-08-23 15:17 CST  
> 当前分支：`multi-agents`  
> 下一阶段目标：基于既有改造计划，按阶段完成企业知识服务多智能体平台的二次开发

## 1. 下一位 Codex 首先要做什么

1. 完整阅读仓库根目录的 `AGENTS.md`，后续所有实现必须遵守其中的架构、测试、安全、兼容和 Git 约束。
2. 完整阅读 `xzh_docs/plan_docs/multi-agent-refactor-plan.md`。该文件是本次二次开发的主计划，不要在本交接文档中寻找重复的详细设计。
3. 确认当前仍处于 `multi-agents` 分支，并检查工作区状态。
4. 从计划的“阶段 0：建立测试基线”开始实施，不要直接跳到 Supervisor 或一次性重写现有系统。
5. 每完成一个阶段，运行该阶段规定的测试和验收检查，更新相关文档后再进入下一阶段。

## 2. 当前项目状态

当前仓库仍基本处于原始实现状态，尚未开始 Multi-Agent 代码改造。本轮只完成了架构分析和项目规范文档。

现有系统主要由以下部分组成：

- FastAPI Web/API 服务。
- 基于 LangChain `create_agent` 的通用工具调用对话 Agent。
- Milvus 文档向量库、Markdown/TXT 上传、切分、Embedding 和检索。
- 基于 LangGraph 的 Plan-Execute-Replan 任务工作流，目前仍使用 AIOps/故障诊断语义。
- 本地知识、时间、Prometheus 工具，以及 CLS/Monitor MCP Server。
- 基于 `MemorySaver` 和 `thread_id` 的进程内会话状态。
- 普通对话和 AIOps 两套 SSE 流式接口及前端处理逻辑。

关键代码入口已经列在 `AGENTS.md` 的“当前项目基线”部分。下一位 Codex 应直接检查代码，不要仅依赖 README 或注释判断功能。

## 3. 已确定的架构决策

项目定位已经从“智能 OnCall/AIOps 系统”调整为：

> **企业知识服务多智能体协同平台**

采用以下目标架构：

- Supervisor Agent：路由、委派、聚合和终止控制。
- Knowledge Agent：Milvus/RAG 检索、证据和引用。
- Data Agent：只读 MCP 与外部数据查询。
- Task Agent：内部包含 Planner、Executor、Replanner 的复杂任务子图。
- Reviewer Agent：完整性、Groundedness、错误披露和一次有限返工。

重要边界：

- Planner、Executor、Replanner 是 Task Agent 内部节点，不是为了包装数量而定义的三个 Agent。
- MCP Server 和工具属于外部能力，不是 Agent。
- 简单问题必须保留快速路径，不能强制经过所有 Agent。
- 并行只用于相互独立的只读任务。
- Agent 之间使用结构化任务、结果和证据契约，不传递全部内部消息或隐藏思维链。
- 旧 `/api/chat`、`/api/chat_stream` 和 `/api/aiops` 在迁移期保持兼容。
- 所有循环、重试、工具调用和图递归必须有硬上限。

完整状态模型、目录规划、图拓扑、工具权限矩阵、API/SSE 契约和验收条件均已记录在主计划中，不在此重复。

## 4. 后续实施顺序

严格按照主计划推进，推荐以以下里程碑组织工作：

- **M1：可测试的基础层**——为现有行为建立测试基线，统一 LLM Factory、Tool Registry、Checkpoint Factory、运行限制和 MCP 生命周期。
- **M2：真实 Specialist**——抽取 Knowledge Agent、Data Agent，并将 AIOps 工作流泛化为 Task Agent 子图。
- **M3：Multi-Agent 主链路**——实现 Supervisor、顶层 LangGraph、结果聚合、Reviewer 和有限返工。
- **M4：产品化交互**——统一 Agent API、SSE 事件和前端执行时间线，同时保留旧接口适配。
- **M5：工程化证据**——增加持久化状态、可观测性、Eval、非运维业务资料和最终 README/简历材料。

下一次开发会话应优先完成阶段 0，至少包括：

- 创建 `tests/unit`、`tests/integration` 和 `tests/contract` 等基础结构。
- 为文档切分、知识结果格式化、MCP 错误清洗增加单元测试。
- 为现有 `/api/chat`、`/api/chat_stream`、`/api/aiops` 建立最小契约测试。
- 使用 Fake LLM、Fake Retriever 和 Fake MCP Tool，确保普通测试不依赖真实外部服务或密钥。
- 记录当前系统的预期行为和已知失败，形成后续重构回归基线。

不要在没有测试基线的情况下同时移动目录、重写状态和更改 API。

## 5. 当前工作区与 Git 状态

交接时：

- 当前分支为 `multi-agents`。
- `HEAD` 仍指向初始提交 `46ab4e1`。
- `AGENTS.md` 是本轮新增的未跟踪文件。
- `xzh_docs/plan_docs/` 是本轮新增的未跟踪计划目录。
- `xzh_docs/handoff_docs/` 包含本交接文档。
- 本轮没有修改业务代码，没有创建提交，也没有推送远端。

这些未跟踪文档属于用户本轮要求，下一位 Codex 必须保留。除非用户明确要求，不要提交、推送、切换分支或改写 Git 历史。

## 6. 安全提醒

- 本地 `.env` 被 `.gitignore` 忽略且未受 Git 跟踪，但其中曾发现看起来真实的模型服务凭据。本交接文档未包含任何敏感值。
- 不要读取、打印或复制 `.env` 内容。
- 用户应在对应服务控制台轮换可能已暴露的密钥。
- 测试必须通过 Fake 或依赖注入运行，不能依赖真实密钥。

## 7. 验证与完成标准

每次交付至少检查：

- `git branch --show-current`
- `git status --short`
- 最小相关 pytest 测试集
- `make lint`
- 风险允许时运行 `make type-check` 和 `make check-all`

如果受 Milvus、MCP、模型服务或本地环境限制无法运行某项检查，必须明确说明未运行的项目和原因，不能将其描述为通过。

只有完成主计划 M3 且具有可运行代码和自动化测试后，项目才可以对外描述为已实现 Multi-Agent；量化指标必须等到 M5 的 Eval 数据和评估脚本可复现后才能写入简历。

## 8. 建议使用的技能

- **`handoff`**：每个里程碑结束或上下文需要切换时调用，把新增决策、实际完成内容、测试结果和剩余问题写入 `xzh_docs/handoff_docs`。不要复制主计划全文，只引用已有文档并记录增量。
- 当前阶段是本地 Python/LangGraph 工程改造，没有必须额外调用的专用技能。若需要核对快速变化的 LangChain/LangGraph API，应查询官方文档，并只依据官方资料实施。

## 9. 关键文档索引

- 仓库规则：`AGENTS.md`
- 详细改造计划：`xzh_docs/plan_docs/multi-agent-refactor-plan.md`
- 原项目说明：`README.md`
- 原作者简历建议：`xzh_docs/面试简历的写法.md`
- 本交接文档：`xzh_docs/handoff_docs/2026-08-23-multi-agent-refactor-handoff.md`

