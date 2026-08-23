# M5 离线评估说明

`m5_baseline.json` 是在 M5 开始时冻结的 M4 行为契约基线，`m5_acceptance.json` 是 M5
验收快照。两者使用同一份 34 条数据集，文件内记录数据集 SHA-256、五类全部逐案例结果和
实现标签；没有从失败用例中挑选子集。

本轮分数为 34/34，基线与当前均为 1.0，变化为 0。这是预期结果：M5 新增的是持久化、
可观测性和评估基础设施，没有宣称提升 M4 已实现的路由或 Reviewer 契约。重启恢复和 Trace
由 `tests/integration/test_persistence_and_observability.py` 单独验收。

这些数字只表示固定离线契约用例通过率。它们不依赖真实 LLM、Milvus 或在线 MCP，因此不能
解释为回答准确率、检索召回率、Groundedness、线上成功率或生产性能。未来改变路由、检索、
工具选择、Task 或 Reviewer 行为时，应保留本基线并运行：

```bash
make eval
```

新报告写入忽略 Git 的 `artifacts/evals/latest.json`；若要提交新的正式快照，必须同时评审
数据集差异和逐案例差异。
