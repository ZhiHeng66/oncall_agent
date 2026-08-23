# M4 统一 API、SSE 与前端实施交接

> 完成时间：2026-08-23 16:55 CST
> 分支：`multi-agents`
> 范围：阶段 7（统一 API、SSE 与前端）

## 1. 完成内容

- 新增 `/api/agent/run` 与 `/api/agent/run_stream`，所有业务请求统一进入 M3 顶层
  Supervisor-Specialist LangGraph，调用方不再选择 Chat/AIOps 业务模式。
- 新增运行和线程查询接口：`GET /api/agent/runs/{run_id}`、
  `GET /api/agent/threads/{thread_id}` 和 `DELETE /api/agent/threads/{thread_id}`。
  当前存储仅为进程内开发状态。
- 新增类型化 API 模型，统一 JSON 响应包含 `run_id`、`thread_id`、状态、答案、
  标准化来源、参与 Agent、路由摘要、清洗错误和终止原因。
- 新增统一 SSE 信封，每条事件包含 `event_id`、`run_id`、`thread_id`、`type`、
  `timestamp` 和 `data`，可选 `agent` 字段只使用有限 Agent 名称。
- 顶层图增加实时 custom events，覆盖 Supervisor 路由、Agent 开始/完成、Task 计划与
  步骤、Data 工具开始/完成和 Reviewer 审核。工具事件不发送调用参数或原始返回值。
- 每个事件流只发送一个 `run_completed` 或 `run_failed`。`partial` 使用
  `run_completed(status=partial)` 透明表达，不伪装成完整成功。
- API 检测客户端断开并关闭底层异步生成器；服务层测试验证关闭流会取消正在等待的
  Runner，不继续产生终态或后续事件。
- 公共来源只展示有限长度的脱敏 excerpt 和允许的元数据字段；答案、来源、事件和错误
  均过滤常见 Token、Authorization、Cookie 等敏感值。
- `/api/chat` 与 `/api/chat_stream` 已改为 `MultiAgentService` 兼容适配器，继续接受
  `Id`/`Question` 并保留旧 JSON 与 `content/done/error` SSE 形状。
- `/api/aiops` 继续使用通用 Task Agent 兼容适配，并在 OpenAPI 中标记 deprecated；
  旧接口和文件未删除。
- 前端改用统一 Agent JSON/SSE 接口，增加统一 `SSEEventParser`、Agent 执行时间线、
  来源卡片、失败终态处理，并移除 AIOps 业务模式按钮。快速/流式只代表传输方式。
- 页面和 FastAPI 描述更新为企业知识服务多智能体平台；README 同步统一接口、兼容接口、
  请求示例、事件约束和 MemorySaver 限制。
- 将 `.gitignore` 中的生成态 `data/models/datasets/artifacts/checkpoints` 规则锚定到仓库根目录，
  避免误忽略 `app/agent/specialists/data/` 与 `app/models/agent.py`。

## 2. 验收结果

以下命令于 2026-08-23 在 Python 3.13.9 环境通过。测试显式禁用 `.env` 并清空
`DASHSCOPE_API_KEY`，未调用真实 LLM、Milvus 或在线 MCP Server：

```text
node --check static/app.js
  -> JavaScript syntax check passed

ONCALL_DISABLE_ENV_FILE=1 DASHSCOPE_API_KEY='' make check-all
  -> format、Ruff、89 tests 全部通过

ONCALL_DISABLE_ENV_FILE=1 DASHSCOPE_API_KEY='' make type-check
  -> Success: no issues found in 77 source files
```

M4 自动化验收覆盖：

- 新 JSON 接口的 direct、single、parallel、complex 路由透传。
- 新 SSE 信封必需字段、事件 ID、同一 run/thread、一条终态和正常完成。
- 路由、Agent、工具、Task 步骤、Reviewer、内容和终态事件转换。
- 图失败、流异常、敏感字段/正文脱敏、来源元数据白名单和答案脱敏。
- 客户端关闭流后取消底层 Runner。
- 运行查询、线程查询、线程删除及对应开发 Checkpoint 清理。
- 旧 Chat JSON/SSE 和旧 AIOps SSE 契约继续通过。
- 主 FastAPI 应用同时注册新旧路由，AIOps 路由带 deprecated 标记。
- 前端使用统一端点、统一解析器、时间线、来源和两类终态的静态契约。

尝试按 Browser 技能执行本地页面视觉验收，但当前环境没有可用浏览器实例，因此未完成
截图级验证；已使用 `node --check` 和前端静态契约测试作为可复现替代，并明确保留该限制。

测试覆盖率输出仅作为本地基线观测值，不作为质量成绩、生产指标或简历量化指标。

## 3. 当前能力与限制

- 当前已具备可直接调用和展示的统一多智能体 JSON/SSE 产品入口，旧 API 仍兼容。
- `/runs`、`/threads` 和 Checkpoint 仍依赖当前进程内存；服务重启或多 Worker 之间不共享，
  不能描述为持久化运行历史。
- 当前前端展示线性执行时间线、来源和错误，不包含图形化 DAG、Token 曲线或成本面板。
- 当前没有浏览器截图级回归证据；需要在可用浏览器环境中补一次真实视觉与交互检查。
- Eval 数据集仍只有基线请求，没有路由、Reviewer、检索和工具选择的正式评分报告；
  不声明准确率、Groundedness、延迟或并发量指标。
- 旧 RAG/AIOps 服务和兼容事件仍保留，需等待调用方迁移及兼容测试稳定后再申请删除。

## 4. 下一阶段

按主计划进入 M5：持久化 Checkpointer、可观测性、固定 Eval 数据集与报告、非运维业务
资料和最终项目文档。实施前应先选择持久化后端，并固定运行恢复、跨线程隔离和 Eval
重放的验收命令。
