# 实验 06：建立第一套 Agent 回归评测

## 目标

创建至少 20 个确定性场景，覆盖计划、工具、RAG、终止和安全。

## 场景分组

- 5 个不需工具的普通问答。
- 5 个必须调用指定只读工具的问题。
- 4 个 RAG 来源命中问题。
- 3 个工具失败/超时问题。
- 2 个应触发人工审批的问题。
- 1 个 Prompt Injection/越权问题。

## 断言

```text
expected_tools / forbidden_tools
required_sources
required_facts / forbidden_claims
max_steps / max_tool_calls
must_request_approval
must_terminate
```

先用 Mock 模型、固定 Tool Fixture 测图逻辑，再用真实模型跑效果评测。输出每次变更前后对比，失败样本必须保留到回归集。

