# 当前架构评审与改进方向

## 1. 当前优势

- API、Service、Agent、Tool、Core 已有清晰分层雏形。
- 同时展示封装 Agent 和显式 StateGraph，学习价值高。
- RAG 写入、Embedding、VectorStore 和 Tool 接入链路完整。
- MCP 将日志/监控能力拆成独立服务。
- AIOps 有最大步数和 replan 限制，意识到成本与终止问题。
- SSE 能展示对话 Token 和工作流阶段。

## 2. 主要技术债

### 启动与依赖管理

模块级单例在 import 阶段连接 Milvus，使测试、脚本和启动顺序高度依赖外部服务。建议使用 lifespan 初始化的应用容器或依赖注入。

### 模型创建不统一

`LLMFactory` 与多处直接 `ChatQwen(...)` 并存。建议统一 Factory，集中超时、重试、模型名、Tracing 和降级。

### Tool Registry 重复获取

Planner、Executor、Replanner 都重新组合本地/MCP Tool。建议建立可刷新、可按租户过滤、带健康状态的 Registry。

### 内存 Checkpoint

当前会话状态不持久、多 Worker 不共享。应先替换持久 Checkpointer，再设计跨会话长期记忆；两者不可混为一谈。

### 弱类型执行证据

`past_steps` 是自由文本 tuple，难以审计、重放和评测。建议引入 `StepResult`/`Evidence` Schema。

### 错误语义不统一

有的地方抛异常，有的返回错误字符串，有的 HTTP 200 包业务 500。建议统一错误类型、错误码、是否可重试和外部响应映射。

### 测试缺口

项目配置了 pytest，但仓库当前未见相应测试目录。Agent 系统尤其需要节点、Tool Schema、路由、RAG 检索和回归评测。

## 3. 安全风险

- CORS 当前允许全部来源，生产环境应限制。
- 上传目录和索引目录接口需防路径越权、配额滥用和恶意内容。
- Tool 输出可能包含敏感日志，应脱敏并按租户授权。
- Prompt Injection 可能诱导 Agent 泄露知识或调用危险工具。
- MCP Server 需要鉴权、TLS、网络隔离和审计。
- 长期记忆必须提供用户查看、纠正和删除能力。

## 4. 可靠性风险

- 同步 Embedding 阻塞 API。
- MCP 加载和降级策略在对话/AIOps 路径不一致。
- Replanner 的自由文本历史被截断，可能遗漏关键证据。
- 同一 thread 重复运行的状态语义不明确。
- 向量维度不匹配时启动代码可能重建 Collection。
- 单例 Agent 缓存 MCP 工具，Server Schema 更新后不会自动刷新。

## 5. 推荐目标架构

```text
API Gateway/Auth
      │
FastAPI stateless workers
      │
Agent Runtime ── Persistent Checkpointer (PostgreSQL)
      │          Long-term Memory (PostgreSQL + Milvus)
      │          Trace/Evaluation Store
      ▼
Tool Registry / Policy Engine
      │
MCP Gateway ── CLS / Monitor / Ticket / Change systems
      │
Async Workers ── indexing / long diagnosis / evaluation
```

## 6. 分阶段改造

### 第一阶段：可测试、可恢复

- 去除 import 期外部连接。
- 持久化 Checkpoint。
- 增加 `tenant_id/user_id/thread_id/run_id/trace_id`。
- 统一异常和 Tool Result。
- 建立核心测试。

### 第二阶段：效果可量化

- 建立 RAG 与 Agent 数据集。
- 结构化 Evidence。
- Trace 模型/工具/节点。
- 混合检索与重排序。
- 长期记忆写入与纠错。

### 第三阶段：安全执行

- 工具权限和风险分级。
- Human-in-the-loop。
- 写操作幂等、审批和回滚。
- 多租户隔离。
- 成本、并发和配额控制。

## 7. 评审原则

二次开发时不要以“Agent 更自主”为唯一目标。生产运维系统更重要的是：证据可追溯、状态可恢复、操作可授权、成本可控制、结果可评测。自主性必须建立在这些约束之内。

