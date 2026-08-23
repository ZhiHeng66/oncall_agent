# 二次开发 07：工具治理

## Tool Registry

为每个工具维护机器可读元数据：

```text
name, version, owner, risk_level, read_only,
required_roles, tenant_scope, timeout, retry_policy,
idempotent, data_classification, enabled
```

Agent 获得的工具列表应经过用户、租户、环境和风险策略过滤，而不是把全部工具直接绑定。

## 统一结果

```json
{
  "status": "success|error|partial",
  "data": {},
  "error": {"code": "...", "message": "...", "retryable": false},
  "observed_at": "...",
  "source": "...",
  "trace_id": "..."
}
```

## 关键控制

- 参数 Pydantic 校验和服务端默认值。
- 网络超时、并发限制、熔断和有限重试。
- 写操作幂等键、审批和审计。
- 输出大小限制、脱敏和内容类型检查。
- 防止工具结果中的 Prompt Injection。
- Tool Schema 版本化和兼容测试。
- MCP Server 健康检查与隔离。

## 命名原则

名称应明确动作和对象，例如 `query_service_logs`；避免模糊名称和多个近义工具。描述陈述适用/不适用场景，不能用宣传性语言。

