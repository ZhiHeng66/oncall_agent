# 实验 04：新增并治理一个 MCP Tool

## 目标

新增只读工具 `get_service_owner`，验证发现、调用、失败重试和 Agent 使用证据。

## 步骤

1. 在测试 MCP Server 定义工具，参数为非空 `service_name`。
2. 返回统一结构：status、data、observed_at、source。
3. Client 单独调用，保存 Schema 和结果。
4. 主应用重新加载工具，确认日志中的可用工具列表。
5. 提交明确问题，捕获 AI tool call、MCP Server 日志和 ToolMessage。
6. 测试未知服务、非法参数、Server 停止和超时。
7. 验证重试次数与总延迟。
8. 在 Registry 中标记 owner、版本、只读、超时和权限。

## 验收

不能只看最终文本。必须能用同一 `trace_id` 关联模型决策、工具调用、Server 执行和最终回答。

