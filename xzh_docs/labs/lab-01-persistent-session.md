# 实验 01：把内存会话改造成可恢复会话

## 目标

在不改变 Agent 业务行为的前提下，把 Checkpointer 创建逻辑抽象出来，并证明服务重启后同一 thread 可恢复。

## 步骤

1. 为当前 `MemorySaver` 写基线测试：同一 thread 两轮消息可读取，不同 thread 隔离。
2. 增加 `CHECKPOINT_BACKEND` 和连接配置。
3. 实现 `create_checkpointer()`，先支持 memory 和 SQLite。
4. 通过构造参数把同一 Checkpointer 或同后端实例注入 Service。
5. 启动服务，创建 `lab-persist-1` 会话并连续问两轮。
6. 完全终止进程后重启，再读取会话接口。
7. 使用两个不同 thread 验证隔离。
8. 模拟 AIOps 执行中断，记录可恢复位置。

## 必须记录

- 重启前后消息数量和类型。
- Checkpoint 存储位置与表结构。
- 同一 thread 重复提交的语义。
- 清除会话是否真正删除所有 Checkpoint。
- 数据库不可用时服务启动/请求行为。

## 验收

重启不丢状态、thread 不串线、clear 生效、错误可观测。完成实验后再评估 PostgreSQL，不要把 SQLite 直接当生产方案。

