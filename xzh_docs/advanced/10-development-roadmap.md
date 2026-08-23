# 二次开发总路线图

## P0：可靠、可测、可恢复

1. 去掉 import 阶段连接，统一 lifespan 资源管理。
2. 建立 `tenant/user/thread/run/trace` 标识。
3. 持久化 Checkpoint。
4. 统一 Model Factory、Tool Registry 和错误结构。
5. 结构化 StepResult/Evidence。
6. 建立节点测试、Tool 测试和核心冒烟测试。
7. 加基础 Trace、指标和脱敏。

完成定义：重启和多 Worker 不丢会话；主要失败可定位；每次结论能追到工具证据。

## P1：效果可量化

1. RAG 标注集、混合检索、Reranker 和引用。
2. Agent 过程/结果评测集。
3. 最小长期记忆：显式保存用户偏好，可查看删除。
4. 历史事故经验记忆。
5. Prompt、模型和 Tool Schema 版本化。

完成定义：每次变更有量化对比；记忆不会跨租户泄漏；错误记忆可纠正。

## P2：安全执行与规模化

1. 工具风险分级和 Policy Engine。
2. Human-in-the-loop 和持久中断恢复。
3. 异步任务、并行步骤和预算限制。
4. 多租户权限、配额和审计。
5. 索引蓝绿切换、灾备和数据生命周期。

完成定义：高风险操作未经授权不能执行；长任务可恢复；系统能在明确 SLO 和成本边界内运行。

## 推荐首个迭代

两周内优先完成：

```text
第 1～2 天：核心链路测试 + trace id
第 3～5 天：Checkpointer Factory + SQLite 重启实验
第 6～8 天：PostgreSQL Checkpoint + thread owner
第 9～10 天：结构化 Tool Result/Evidence
```

长期记忆放在持久会话和身份隔离稳定之后。否则记忆即使“能存”，也会产生归属不明、无法删除和跨用户泄漏等更严重问题。

