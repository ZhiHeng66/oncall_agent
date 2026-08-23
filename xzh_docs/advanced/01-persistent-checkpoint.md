# 二次开发 01：持久化会话与工作流 Checkpoint

## 1. 问题定义

当前 `RagAgentService` 和 `AIOpsService` 都使用 `MemorySaver`。它能按 `thread_id` 保存状态，但数据只存在当前进程：

- 重启后丢失。
- 多 Uvicorn Worker 彼此看不到。
- 容器迁移或扩缩容后无法恢复。
- 长任务中断后不能可靠接续。

第一步不是马上做“长期记忆”，而是把现有 Checkpoint 持久化。

## 2. Checkpoint 保存什么

对话 Agent 主要保存消息状态；AIOps 保存 `input/plan/past_steps/response` 以及图执行元数据。它回答“这个 thread 执行到了哪里”，不回答“哪些信息值得跨会话记住”。

```text
Checkpoint = 工作内存和执行快照
Long-term memory = 经选择后保存的跨会话知识
```

## 3. 存储选择

| 后端 | 适用场景 | 局限 |
|---|---|---|
| SQLite | 单机学习、最小实验 | 并发和部署能力有限 |
| PostgreSQL | 生产、多 Worker、事务和审计 | 需要运维数据库 |
| Redis | 短期共享、TTL、低延迟 | 持久性和查询能力需谨慎配置 |

建议开发实验先 SQLite，生产设计使用 PostgreSQL。具体 LangGraph Checkpointer 包和 API 必须以当前 1.0.8 官方接口及安装版本验证，不要照搬旧版教程。

## 4. 抽象工厂

不要在两个 Service 中分别硬编码后端：

```python
def create_checkpointer(settings):
    if settings.checkpoint_backend == "memory":
        return MemorySaver()
    if settings.checkpoint_backend == "sqlite":
        return create_sqlite_checkpointer(settings.checkpoint_url)
    if settings.checkpoint_backend == "postgres":
        return create_postgres_checkpointer(settings.checkpoint_url)
    raise ValueError("unsupported checkpoint backend")
```

由 FastAPI lifespan 创建并注入两个 Service，关闭时统一释放连接。不要在 import 阶段创建数据库连接。

## 5. 标识符设计

最少区分：

```text
tenant_id：组织边界
user_id：用户身份
thread_id：一段对话或一次诊断任务
run_id：thread 内一次执行
trace_id：一次请求的可观测链路
```

只用前端提供的 `session_id` 既容易碰撞，也不能作为权限边界。服务端应从认证信息确定 tenant/user，再校验 thread 归属。

## 6. 新任务与恢复任务

设计清晰接口：

- 创建 thread：服务端生成 ID，写 owner 和状态。
- 向 thread 追加消息：继续同一会话。
- 启动 diagnosis run：产生新的 run。
- 获取状态：只允许 owner/授权角色。
- 恢复中断：从指定 Checkpoint 继续。
- 关闭/归档：不再接受新运行。
- 删除：同时清理 Checkpoint、消息索引和相关长期记忆引用。

避免把“再次传相同 thread id”同时解释成新任务和恢复任务。

## 7. 迁移步骤

1. 为现有两处 `MemorySaver` 写行为测试。
2. 引入 Checkpointer Factory，默认仍为 memory。
3. 加 SQLite 后端跑重启恢复测试。
4. 给 thread 加 owner/tenant 元数据。
5. 迁移到 PostgreSQL 并做并发测试。
6. 配置 TTL、归档和删除。
7. 再启用多 Worker。

## 8. 验收测试

- 服务重启后，同一 thread 历史仍在。
- 请求落到不同 Worker，状态一致。
- 两个租户使用相同外部 session 字符串也不会串线。
- AIOps 在 Executor 后崩溃，可从合法位置恢复。
- 重复恢复不会重复执行非幂等工具。
- 删除 thread 后无法再读取相关状态。
- Schema 升级时旧 Checkpoint 有迁移或明确拒绝策略。

## 9. 风险

Checkpoint 可能包含完整对话、工具参数和日志证据，属于敏感数据。需要加密、访问控制、审计、保留期限和删除机制。写操作恢复尤其危险：恢复前必须确认该步骤是否已经在外部系统成功执行。

