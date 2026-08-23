# 二次开发 08：AIOps 工作流生产化

## 从文本历史到结构化任务

建议状态拆分：

```text
TaskContext：目标、租户、服务、时间窗
PlanStep：id、目标、依赖、候选工具、状态
Evidence：工具、参数、时间、原始结果引用
Decision：continue/replan/respond + reason
FinalReport：结论、置信度、引用、失败项
```

## 执行能力

- 无依赖步骤可并行，但设置并发上限。
- 每步有超时、重试、取消和补偿策略。
- 失败区分 required/optional 证据。
- 总任务有最大时间、步骤、Token 和费用预算。
- 写操作通过 HITL。
- 长任务交给异步 Worker，API 返回 job id。

## 恢复

Checkpoint 恢复必须记录每个 Step 的外部副作用状态。只读查询可安全重跑；写操作先用幂等键向目标系统查询是否已执行。

## 报告可信度

每个根因结论关联 Evidence ID，区分 observed/inferred/unknown。报告中的“已执行”只能来自成功工具调用，不能来自模型计划文本。

