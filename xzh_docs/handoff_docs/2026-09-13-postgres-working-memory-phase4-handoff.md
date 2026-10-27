# PostgreSQL 工作记忆与压缩最终交接

日期：2026-09-13  
状态：四阶段实施计划已完成并通过最终验收。

## 最终能力边界

- PostgreSQL persistence 路径提供按 `thread_id` 的结构化案件工作记忆：跨重启加载、最近公开对话尾部、角色化上下文、确定性写回与 CAS 冲突重试。
- 当快照序列化大小达到 `WORKING_MEMORY_COMPRESSION_THRESHOLD` 时，压缩器经共享 `LLMFactory` 请求 `WorkingMemoryUpdate` 结构化输出。
- 模型输出只允许贡献 `summary_candidate`。新增事实、实体、验证等级、Evidence 引用、时间字段、状态和删除意图一律忽略；实际快照仍由确定性逻辑裁剪。
- 确定性裁剪优先移除未验证或非活跃历史项，绝不修改保留 Evidence 的来源引用、验证等级或时间语义。无法在不移除已支持观察的前提下安全缩小，则保留原快照。
- 压缩超时、供应商异常、空摘要或非法结构会降级为未压缩快照；主回答和最后合法记忆不受影响。

## 仍然不是的能力

- 不是多租户授权、用户画像、跨线程长期记忆或语义向量记忆。
- Memory/SQLite persistence 模式不提供正式的工作记忆后端。
- run/events 与工作记忆是顺序持久化：先保存公开 run/events，再 CAS 更新记忆；并非跨表单事务。
- 没有新增公共 API 或 SSE 事件，既有 `/api/agent/*` 与 `/api/chat*` 契约保持兼容。

## 验收证据

- 单元测试覆盖：压缩阈值、结构化摘要、模型越权字段被忽略、异常降级、Reducer 的去重/supersede/来源保留。
- 服务集成测试覆盖：JSON/SSE 共用写回、CAS 冲突重试、记忆失败不伪装主回答失败。
- PostgreSQL 集成测试覆盖：Store、服务重启读取最后快照、`clear_thread()` 同步删除。
- 最终检查：`make test-quick`、`make lint`、`make type-check`、`git diff --check`；并使用一次性临时 PostgreSQL 容器运行 PostgreSQL 集成测试。
