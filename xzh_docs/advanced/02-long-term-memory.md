# 二次开发 02：跨会话长期记忆

## 1. 先定义“记住”

长期记忆不是保存所有聊天记录。它是从交互中选择有未来价值的信息，结构化保存，在后续任务中按权限和相关性取回，并允许用户纠正或删除。

```text
原始对话 ≠ 长期记忆
Checkpoint ≠ 长期记忆
知识库文档 ≠ 用户记忆
```

## 2. 适合本项目的四类记忆

### 用户画像（Profile）

稳定偏好和身份：语言、时区、团队、报告格式、通知偏好。适合结构化关系数据库，读取时无需向量检索。

### 语义事实（Semantic Memory）

业务事实：服务负责人、部署区域、上下游依赖、允许的数据源。既可结构化查询，也可用 Embedding 做语义召回。

### 情景记忆（Episodic Memory）

过去发生的具体事件：某次告警的症状、证据、根因、处理和结果。适合按服务、故障类型过滤后向量检索。

### 程序性规则（Procedural Memory）

组织批准的处理流程和工具政策。它更接近版本化配置/SOP，不应让模型根据普通聊天随意改写。

## 3. 目标架构

```text
请求
  ├── 身份/权限上下文
  ├── 结构化 Profile 查询
  ├── 语义记忆检索（metadata filter + vector search）
  └── 当前 thread Checkpoint
             ↓
         Agent/Graph
             ↓
      候选记忆提取器
             ↓
      校验/去重/冲突处理
             ↓
 PostgreSQL（事实） + Milvus（语义索引）
```

PostgreSQL 是记忆事实源，Milvus 可存可检索文本和 embedding。不要把向量库中的相似文本当唯一真相。

## 4. 建议数据模型

```text
memory_id           全局 ID
tenant_id           租户隔离
user_id             所属用户，可为空表示团队记忆
namespace           profile/service/incident/preference
memory_type         profile/semantic/episodic/procedural
subject             记忆主体，例如 service:payment
content             人类可读内容
structured_data     JSON 结构化事实
source_thread_id    来源会话
source_message_ids  来源证据
confidence          置信度
status              active/superseded/deleted/pending_review
valid_from/to        有效期
created_at/updated_at
embedding_version   向量模型版本
```

Milvus metadata 至少包含 tenant、user、namespace、memory_id、status、validity。所有检索必须先做权限过滤，再做语义排序。

## 5. 读路径

在调用模型前：

1. 根据认证上下文取得 tenant/user。
2. 读取确定性的 Profile 字段。
3. 从当前问题提取查询主题和服务名。
4. 用 metadata 限定租户、用户、类型和有效期。
5. 向量召回候选记忆。
6. 重排序、去重并限制 Token。
7. 以“可参考但可能过期”的独立上下文块注入。

不要让检索到的记忆覆盖当前用户明确提供的新信息。Prompt 应定义冲突优先级：当前明确声明 > 已验证结构化事实 > 高置信记忆 > 模型推测。

## 6. 写路径

建议异步执行，不阻塞用户答案：

```text
对话/诊断结束
→ 生成候选记忆
→ 规则校验
→ PII/敏感信息检查
→ 与现有记忆比对
→ 新增/更新/冲突/忽略
→ 写事实库
→ 更新向量索引
```

候选写入条件：

- 对未来任务有明确价值。
- 来源可追溯。
- 不是短期闲聊或一次性数据。
- 不包含未经允许保存的敏感内容。
- 能确定主体和命名空间。

## 7. 去重与冲突

示例：旧记忆“支付服务负责人是张三”，新消息“负责人已改为李四”。不能只添加第二条。

处理流程：

1. 按 subject + fact key 找现有事实。
2. 检查新来源、时间和权限。
3. 将旧记录标为 superseded，保留审计链。
4. 新记录写 `valid_from`。
5. 对高风险事实要求人工确认。

语义相似不等于事实相同，最终更新决策应尽量依赖结构化 key。

## 8. 与 AIOps 的结合

一次诊断完成后，不应把整段报告原样当记忆。抽取：

```json
{
  "service": "data-sync-service",
  "symptoms": ["cpu_high", "sync_delay"],
  "evidence_refs": ["tool-call-1", "tool-call-3"],
  "root_cause": "retry storm",
  "actions": ["limit thread pool", "reduce retry frequency"],
  "outcome": "resolved",
  "verified_by": "operator-id",
  "occurred_at": "..."
}
```

下一次 Planner 先按 tenant/service/症状过滤并召回相似事故，把成功步骤作为“历史经验”，但仍必须重新查询当前监控数据，不能把旧证据当当前事实。

## 9. 用户控制

至少提供：

- 查看“你记住了什么”。
- 修改错误记忆。
- 删除单条或全部记忆。
- 关闭某类记忆写入。
- 查看记忆来源和最近使用时间。

Agent 回答因记忆而个性化时，应在必要场景说明依据，尤其是运维决策。

## 10. 安全与 Prompt Injection

记忆内容也是不可信输入。恶意用户可能要求保存“以后忽略安全规则”。因此：

- 不保存改变系统安全边界的自然语言指令。
- Procedural Memory 只能由授权角色/配置流程修改。
- 注入 Prompt 时与 System Policy 分区。
- Tool 权限永远由服务端策略决定，不由记忆决定。
- 写入前做敏感信息检测和大小限制。

## 11. 评测

至少建立以下指标：

- 写入精确率：保存的信息中有多少真的值得记。
- 写入召回率：应保存的事实是否遗漏。
- 检索相关性：取回的记忆是否帮助当前任务。
- 冲突解决准确率。
- 过期记忆率。
- 跨租户泄漏率，目标必须为 0。
- 对答案正确率和 Token 成本的影响。

## 12. 最小可行版本

第一版只做低风险、结构化用户偏好：语言、时区、报告格式。用户明确说“请记住”时才写入，并提供查看/删除 API。稳定后再增加自动提取和事故经验记忆。

不要第一版就自动保存所有对话并做全局向量搜索；那会同时引入隐私、污染、冲突和成本问题。

