# PostgreSQL 工作记忆阶段三交接

日期：2026-09-13  
状态：阶段三已完成并通过验收；阶段四的 LLM 压缩尚未开始。

## 已实现

- 新增 `WorkingMemoryReducer`：把本轮用户公开请求、成功 Agent Evidence 与明确的执行错误转换为受限 `WorkingMemoryUpdate`，再确定性归并为下一版 `WorkingMemoryContext`。
- 用户请求仅保存为 `user_claim/unverified`；成功 Evidence 才生成带 `MemoryEvidenceRef` 的 `evidence_observation/supported`。工具失败只形成待确认项，不会写成“无告警”等成功事实。
- 同查询范围的新动态观察会将旧观察标为 `superseded`；重复活跃陈述不会重复写入。
- JSON 与 SSE 在同一完成路径中先持久化公开 run/events，再尝试更新工作记忆。记忆写入异常只记录清洗后的服务端日志，不改变已生成的主回答。
- PostgreSQL Store 使用 create/CAS。CAS 失败会重新加载快照、重放同一个确定性增量，并在配置的有限次数内重试。
- `clear_thread()` 同时删除 PostgreSQL 工作记忆快照。

## 事实边界

- run/events 与工作记忆目前通过同一服务完成顺序写入：公开运行先成功保存，随后进行可失败且可重试的记忆更新；这不是跨表的单一数据库事务。若记忆更新失败，最后一个合法快照保持不变，主回答仍真实返回。
- 尚未调用 LLM 做摘要或压缩；`compressed_summary` 沿用已有值。阶段四才会引入受约束的结构化压缩。
- Memory/SQLite persistence 模式仍不会启用正式跨轮工作记忆写回。

## 验收证据

- Reducer 单元测试覆盖：可追溯 Evidence 条目、重复去重、动态观察 supersede、失败工具不伪造成功事实。
- 服务集成测试覆盖：JSON/SSE 共用写回路径、CAS 首次冲突后重试、主回答不因记忆冲突而失败。
- PostgreSQL 集成测试覆盖：服务实例间读取写入后的快照、应用重启后读取，以及 `clear_thread()` 删除快照。
- 已通过：`make test-quick`（126 passed，未配置 PostgreSQL 时 8 skipped）、`make lint`、`make type-check`、`git diff --check`；临时 PostgreSQL 容器中的 `tests/integration/test_postgres_persistence.py` 为 8 passed。

## 下一阶段

阶段四只应在现有确定性裁剪达到阈值时调用共享 LLM Factory 的结构化压缩器；其输出必须重新经过本 Reducer 的引用、验证等级、时间语义和长度校验。
