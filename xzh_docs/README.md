# SuperBizAgent 五天学习与二次开发手册

这套文档以当前仓库的实际源码为准，目标不是孤立地介绍 FastAPI、LangChain 或 LangGraph，而是帮助你在五天内建立从 HTTP 请求、Agent 推理、工具调用、RAG 检索到 AIOps 工作流的完整心智模型，并能继续做工程化改造。

## 适合的读者

你应当已经具备 Python 基础，知道 LLM、Prompt、Agent 和 RAG 大致是什么，但不要求使用过本项目中的框架。每天建议投入 4～6 小时：约 2 小时阅读、2 小时跟代码和日志、1～2 小时完成实验。

## 推荐阅读顺序

| 天数 | 主文档 | 当天产出 |
|---|---|---|
| 准备 | [项目全景](00-project-map.md) | 能画出组件图和两条主链路 |
| 第 1 天 | [FastAPI 与运行时](day-1-fastapi-runtime.md) | 跟踪一次普通请求和一次 SSE 请求 |
| 第 2 天 | [LangChain 工具型 Agent](day-2-langchain-agent.md) | 新增并验证一个本地工具 |
| 第 3 天 | [RAG 与 Milvus](day-3-rag-and-milvus.md) | 手工验证文档写入和检索链路 |
| 第 4 天 | [LangGraph 工作流](day-4-langgraph-workflow.md) | 解释 State、Reducer、Checkpoint 和条件边 |
| 第 5 天 | [MCP 与 AIOps](day-5-mcp-and-aiops.md) | 跑通一次 Plan–Execute–Replan 诊断 |

在进入第 2 天前，建议先阅读 [两类 Agent 的端到端调用链](05-agent-call-chains.md)：其中包含精确到代码行的 Mermaid 流程图。

五天之后继续阅读：

- [RAG 深度学习：从上传文档到生成答案](10-rag-deep-dive.md)：用两条带代码定位的 Mermaid 调用链拆解当前 RAG 实现与边界。
- [源码导读](06-source-code-walkthrough.md)：按目录理解职责和依赖方向。
- [调试手册](07-debugging-guide.md)：按现象定位 FastAPI、模型、MCP、Milvus 和 SSE 问题。
- [扩展手册](08-extension-guide.md)：新增工具、模型、节点和接口。
- [架构评审](09-architecture-review.md)：区分当前事实、局限和建议。
- [二次开发路线图](advanced/10-development-roadmap.md)：从演示系统走向生产系统。
- [长期记忆](advanced/02-long-term-memory.md)：设计跨会话用户记忆和故障经验记忆。

## 完整文档索引

二次开发专题：

- [持久化 Checkpoint](advanced/01-persistent-checkpoint.md)
- [跨会话长期记忆](advanced/02-long-term-memory.md)
- [RAG 质量提升](advanced/03-rag-quality-improvement.md)
- [Agent 评测](advanced/04-agent-evaluation.md)
- [可观测性](advanced/05-observability.md)
- [Human-in-the-loop](advanced/06-human-in-the-loop.md)
- [工具治理](advanced/07-tool-governance.md)
- [AIOps 工作流生产化](advanced/08-workflow-productionization.md)
- [多用户与多租户](advanced/09-multi-user-architecture.md)
- [二次开发总路线图](advanced/10-development-roadmap.md)

动手实验：

- [持久化会话实验](labs/lab-01-persistent-session.md)
- [长期用户记忆实验](labs/lab-02-user-memory.md)
- [RAG 重排序实验](labs/lab-03-rag-reranking.md)
- [新增 MCP Tool 实验](labs/lab-04-add-mcp-tool.md)
- [人工审批实验](labs/lab-05-human-approval.md)
- [Agent 回归评测实验](labs/lab-06-agent-evaluation.md)

附录：

- [依赖地图](appendices/dependency-map.md)
- [术语表](appendices/glossary.md)
- [API 与事件速查](appendices/api-reference.md)
- [命令与代码速查](appendices/cheat-sheet.md)

## 学习方法

每个主题都按下面的顺序学习：

```text
通用原理 → 框架抽象 → 本项目代码 → 运行时证据 → 动手修改
```

不要只读代码。每读完一条调用链，至少做一次以下动作：打日志、修改一个参数、制造一个错误、查看实际返回对象，或者画出调用时序。

建议创建个人学习分支，并保留每天的实验提交。涉及 API Key 的 `.env` 不要提交到 Git。

## 五天验收标准

完成后应能独立回答：

1. `/api/chat_stream` 从请求进入到浏览器显示经历了哪些函数和数据格式？
2. 模型如何知道有哪些工具，工具调用结果如何回到模型？
3. 一篇 Markdown 如何变成 Milvus 中的向量，查询又如何召回它？
4. LangGraph 节点返回的是完整状态还是状态增量？`past_steps` 为什么不会被覆盖？
5. Planner、Executor、Replanner 分别负责什么，流程如何避免无限执行？
6. 本地 Tool 和 MCP Tool 如何被统一交给 Agent？
7. 为什么 `MemorySaver` 不等于长期记忆？服务重启后怎样恢复会话？
8. 如何新增一个受权限控制、可观测、可测试的工具？

## 版本基线

教程以 `uv.lock` 的锁定版本和当前源码为基线，而不是依赖声明中的最低版本。关键版本包括 LangChain 1.2.10、LangGraph 1.0.8、FastAPI 0.128.8、Pydantic 2.12.5、PyMilvus 2.6.9、FastMCP 2.14.5。框架升级时，先阅读 [依赖地图](appendices/dependency-map.md)，再验证本文示例。

## 文档约定

- “当前实现”表示仓库现在真正执行的代码。
- “设计意图”表示 README、注释或命名表达的目标，不保证已经接入。
- “建议改造”表示二次开发方案，不应误认为当前能力。
- 代码路径均相对于仓库根目录。
- 示例中的密钥、地址和业务数据均需替换为自己的配置。
