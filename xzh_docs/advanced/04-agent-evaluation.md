# 二次开发 04：Agent 评测与回归测试

## 三层评测

```text
过程：计划、工具选择、参数、终止
证据：RAG/工具返回是否正确且被忠实使用
结果：最终答案是否完成任务
```

只评最终文字会掩盖“碰巧答对”或“调用了危险工具”的问题。

## 数据集格式

```json
{
  "case_id": "cpu-001",
  "input": "诊断 payment CPU 告警",
  "fixtures": {"alerts": [], "metrics": {}, "logs": []},
  "expected_tools": ["query_cpu_metrics"],
  "forbidden_tools": ["restart_service"],
  "required_facts": [],
  "termination_max_steps": 6
}
```

测试使用 Mock Tool 和固定模型响应，单元测试不依赖线上 API；另保留少量真实模型集成测试。

## 指标

- 计划必要步骤覆盖率、冗余率。
- 工具选择和参数准确率。
- 无依据陈述率。
- 成功终止率和平均步骤数。
- 延迟、Token、模型/工具调用次数。
- 高风险工具误调用率。
- RAG Recall@K 和引用正确率。

## 回归门禁

Prompt、模型、Tool 描述、Chunk 策略或工作流变更都运行相同数据集。先定义允许退化阈值，再决定是否发布。LLM Judge 可辅助评分，但关键安全规则必须用确定性断言。

