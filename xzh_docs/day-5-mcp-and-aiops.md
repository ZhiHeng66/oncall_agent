# 第 5 天：MCP、Plan–Execute–Replan 与综合实战

## 今日目标

完成后你应能解释远程工具怎样进入 LangChain Agent，独立排查 Transport 问题，并完整评审一次 AIOps 诊断的计划、工具证据、重规划和最终报告。

## 1. MCP 解决什么问题

没有 MCP 时，每接一个日志、监控或工单系统都要写一套私有客户端和 Tool 包装。MCP 为能力发现和调用提供统一协议：

```text
Agent Host
→ MCP Client
→ Transport
→ MCP Server
→ Tool / Resource / Prompt
```

本项目主要使用 Tool。`cls_server.py` 和 `monitor_server.py` 是独立进程，主 FastAPI 通过 `MultiServerMCPClient` 连接。

## 2. Server、Client 与 Adapter

- FastMCP Server：把 Python 函数暴露为 MCP Tool。
- MCP Client：连接一个或多个 Server，发现 Schema 并发起调用。
- langchain-mcp-adapters：把 MCP Tool 适配为 LangChain `BaseTool`。

适配之后：

```python
all_tools = local_tools + mcp_tools
```

模型不需要知道工具是在本进程还是远程进程，但工程层必须知道：远程工具有网络延迟、连接失败、鉴权和协议兼容问题。

## 3. Transport

项目配置支持：

- `stdio`：Client 启动子进程，通过标准输入输出通信。
- `sse`：旧式基于 SSE 的传输。
- `streamable-http`：当前本地 FastMCP 使用的 HTTP 方式。

默认本地地址：

```text
http://localhost:8003/mcp  → cls
http://localhost:8004/mcp  → monitor
```

URL 和 Transport 必须匹配。`suggest_mcp_transport()` 只给警告，不自动改配置。典型错误是给 `/sse` URL 配 `streamable-http`，或给 `/mcp` 路径配 `sse`。

## 4. Client 单例与延迟加载

`_mcp_client` 是模块级单例。第一次调用 `get_mcp_client()` 时创建 `MultiServerMCPClient`，之后复用。

对话 Agent 在第一次请求才 `_initialize_agent()`，此时加载 MCP 工具。如果失败，它使用 `load_mcp_tools_safe` 降级为本地工具。

AIOps Planner/Executor/Replanner 目前直接调用 `mcp_client.get_tools()`，部分路径没有相同的安全降级包装，因此 MCP 不可用时行为可能不一致。二次开发应统一 Tool Registry 和错误策略。

## 5. Retry Interceptor

`retry_interceptor` 包裹每次 MCP Tool Call：最多三次，等待时间按 1、2 秒指数增长。最终失败后不抛异常，而返回 `CallToolResult(isError=True)`。

这会把基础设施失败转成模型可见的工具结果。优点是 Agent 可以继续并如实报告；风险是上层若没检查 `isError`，可能把错误文本当业务数据。

只有满足以下条件才适合自动重试：

- 工具只读，或写操作具有幂等键。
- 错误是暂时性的。
- 总超时可控。
- 重试被记录和计量。

## 6. Planner：从目标到计划

Planner 输入：

```text
原始任务
+ RAG 经验文档
+ 本地/MCP 工具描述
+ 规划规则
```

输出 `Plan(steps=[...])`。计划应描述目标和适用工具，但不在 Planner 中真正调用业务工具。当前 Planner 会先调用知识库，因此静态 SOP 能影响步骤。

评审计划时问：

1. 每步是否贡献新证据？
2. 参数是否足够明确？
3. 步骤依赖是否合理？
4. 是否包含纯粹为了“看起来完整”的冗余步骤？
5. 是否可能执行高风险写操作？

## 7. Executor：执行一个步骤

Executor 只取 `plan[0]`：

```text
当前步骤
→ llm.bind_tools(all_tools)
→ 模型产生 0..N 个 tool_calls
→ ToolNode 并处理工具消息
→ 再调模型总结该步
→ past_steps 追加结果
```

它没有把之前所有步骤结果放入当前执行 Prompt。模型只能看到当前步骤，跨步骤上下文主要由 Planner 在步骤文字中预先表达，或者最终 Replanner 汇总。若后续步骤需要上一步的具体动态值，当前结构可能不足，应把相关历史或结构化证据传入 Executor。

## 8. Replanner：控制终止

Replanner 接收执行历史摘要（每个结果截取前 300 字符）、剩余计划和工具描述，输出 `Act`：

- 信息足够：`respond`
- 剩余步骤必要：`continue`
- 原计划严重有误：`replan`

代码还有硬约束：新计划不能比当前剩余计划长；执行达到 5 步后禁止 replan；达到 8 步强制回答。这些规则控制成本和死循环，但也可能过早结束，需要用任务集评测而不是凭感觉修改。

## 9. 最终报告与证据

最终报告 Prompt 要求基于执行历史、不编造、失败要说明。但当前 State 中证据主要是自由文本 tuple：

```python
(step_description, result_text)
```

生产化建议使用结构化 Evidence：

```json
{
  "tool": "query_cpu_metrics",
  "arguments": {"service": "payment"},
  "observed_at": "...",
  "status": "success",
  "data": {...},
  "source": "monitor-mcp"
}
```

这样才能审计“结论是否由真实工具数据支持”。

## 10. AIOps SSE

图以 `updates` 模式运行，Service 把节点更新格式化为计划、步骤完成、重规划和完成事件，API 再编码为 SSE。前端显示的是工作流进度，不是每个报告 Token。

一次理想事件序列：

```text
plan
step_complete
replan/continue
step_complete
...
response_generated
complete
```

实际事件名应以 `_format_*_event` 和运行输出为准。修改节点输出时同步维护事件格式和前端。

## 11. 综合实验：新增 MCP 能力

目标：新增一个只读的“查询服务负责人” MCP Tool，并让 AIOps 在相关任务中使用。

步骤：

1. 在测试 MCP Server 定义 `get_service_owner(service_name: str)`。
2. 写清工具适用场景、空值和未知服务返回格式。
3. 启动 Server，通过 Client 列出工具名和 Schema。
4. 确认 Planner 的工具描述中出现它。
5. 发起“诊断并通知负责人”的测试任务。
6. 从日志验证：计划引用工具、Executor 产生 tool call、结果进入 `past_steps`。
7. 停止 MCP Server，验证重试、降级和最终报告。
8. 为工具增加超时、trace id 和测试。

完成标准不是“报告提到了负责人”，而是有运行证据证明数据来自该 Tool。

## 12. 五天总复盘

请独立画出：

```text
用户请求
→ FastAPI/Pydantic
→ SSE 或 JSON
→ LangChain Agent / LangGraph Workflow
→ ChatQwen
→ Local Tool / MCP Tool
→ RAG/Milvus 或远程系统
→ State/Checkpoint
→ 最终响应
```

然后选择一次真实诊断，回答：

- 哪次调用是模型推理，哪次是工具执行？
- 每个节点输入和输出是什么？
- 哪些结论有证据，哪些只是模型总结？
- 失败时在哪一层重试或降级？
- 服务重启后哪些数据保留，哪些丢失？

## 13. 今日自测

1. MCP Adapter 的价值是什么？
2. URL 和 Transport 不匹配会有什么表现？
3. 为什么写工具不能无条件重试？
4. Executor 当前如何处理第二轮工具调用需求？
5. Replanner 的硬限制解决了什么，又可能带来什么问题？
6. 如何证明最终结论由真实工具数据支持？

能完成综合实验并从日志还原完整诊断，即完成核心五天。下一步进入源码导读和二次开发篇。

