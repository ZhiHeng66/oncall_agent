# PostgreSQL 工作记忆阶段二交接

日期：2026-09-13  
状态：阶段二已完成并通过验收；阶段三“写回、归并与压缩”尚未开始。

## 已实现

- `RunStore.get_recent_thread()` 在 Memory、SQLite 与 PostgreSQL 后端从已完成运行的公开请求和回答中有界读取最近轮次，并按时间正序返回。
- `MemoryContextBuilder` 将 PostgreSQL 快照和最近公开对话转换为临时 `MemoryContextView`，并按服务端 TTL 将动态旧观察区分为有效事实或过期历史线索。
- `MemoryContextRenderer` 以最小必要原则生成角色化上下文：Knowledge 仅接收静态可复用事实；Data 不接收过期动态正文，只接收条目 ID 和强制重新查询提示；Task、Supervisor、Reviewer 获得各自所需的有界摘要。
- `MultiAgentService` 在 PostgreSQL 工作记忆 Store 可用时，于 JSON 与 SSE 调图前加载相同的快照和最近对话；加载失败会记录清洗后的服务端日志并降级继续，不泄露异常细节。
- `MultiAgentState` 保存本轮临时记忆视图与 `memory_usage`。记忆参与时，direct/single 快路径不再绕过 Reviewer。
- Task 子图 Checkpoint 键改为 `thread_id:run_id:task_id`，避免同一业务线程的不同 run 串状态。

## 事实边界

- 只有 PostgreSQL persistence 路径启用跨轮工作记忆；Memory 和 SQLite 路径不会伪装为已支持。
- 本阶段只读取与使用记忆，**不会创建、更新、压缩或写回** `agent_working_memories`。该闭环属于阶段三和四。
- 上下文来自公开请求/回答和结构化快照，不保存 Prompt、隐藏推理、完整 MCP 返回或完整文档。
- `user_id/thread_id` 尚未构成认证或多租户授权边界。

## 验收证据

- 工作记忆上下文单元测试覆盖：动态 TTL 过期分流、公开对话上限、Data 不接收过期正文。
- 服务集成测试覆盖：预置 Store 快照同时传入 JSON 与 SSE 图调用。
- 图集成测试覆盖：现有路由、Reviewer、Task 流与 run 隔离仍正常；Task 运行键包含 run ID。
- 本阶段相关测试命令通过：

  `pytest -q tests/unit/test_working_memory_context.py tests/unit/test_working_memory_contracts.py tests/unit/test_working_memory_store.py tests/unit/test_supervisor_agent.py tests/integration/test_multi_agent_graph.py tests/integration/test_multi_agent_service.py tests/integration/test_persistence_and_observability.py tests/integration/test_task_agent.py`

## 下一阶段

阶段三应实现纯函数 Reducer、从已审计运行结果生成 `WorkingMemoryUpdate`、PostgreSQL 事务/CAS 写回和 JSON/SSE 共用完成路径。开始前应保持本阶段的只读加载边界，不让模型直接覆盖快照。
