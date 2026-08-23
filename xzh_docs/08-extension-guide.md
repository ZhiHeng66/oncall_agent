# 常见扩展开发手册

## 1. 新增本地 Tool

步骤：

1. 在 `app/tools` 新建或选择模块。
2. 使用 `@tool`，完整标注参数和返回类型。
3. Docstring 说明何时使用、限制和时间语义。
4. 对外部请求设置超时。
5. 设计稳定、可识别失败的返回结构。
6. 注册到 `DEFAULT_LOCAL_AGENT_TOOLS` 或专用 Registry。
7. 写直接调用测试和 Agent 选择测试。
8. 验证日志不泄密。

推荐返回：

```python
{
    "status": "success",
    "data": {...},
    "observed_at": "...",
    "source": "...",
}
```

不要让写操作工具只凭自然语言参数立即执行。至少加入权限、审批、幂等和审计。

## 2. 新增 MCP Tool

1. 在 MCP Server 定义工具及 Schema。
2. 独立启动并用 Client 验证工具发现。
3. 确认主应用配置的 URL/Transport。
4. 重启或刷新 Agent 工具缓存。
5. 验证 Tool 已进入 Planner 描述和 Executor 绑定列表。
6. 测试 Server 不可用、超时、错误结果和重试。

不要同时创建同名本地 Tool 与 MCP Tool，否则选择和路由可能歧义。

## 3. 新增 LangGraph 节点

先写状态契约，再写节点：

1. 新字段是否覆盖还是追加？
2. 谁负责初始化？
3. 哪些节点读取/写入？
4. 是否可序列化进 Checkpoint？
5. 节点失败后怎样恢复？
6. 新边会不会形成无界循环？

例如增加 `validator`：在 Replanner 生成 response 后核对证据，再决定 END 或返回 Executor。需同步修改 Graph 边、事件、测试和前端展示。

## 4. 切换模型

当前存在 `ChatQwen` 直连和 `LLMFactory(ChatOpenAI)` 两种创建方式。建议先统一 Model Factory，再切换供应商。验证：

- Tool Calling Schema。
- Structured Output。
- 流式 chunk 字段。
- 中文能力和长上下文。
- 超时、重试和用量字段。
- 相同评测集上的效果与成本。

Embedding 模型更换还涉及向量维度和全量重建，不能只改模型名。

## 5. 新增 API

保持边界清晰：

```text
Pydantic Request
→ Router（协议）
→ Service（业务）
→ Repository/Client（外部依赖）
→ Pydantic Response
```

同步补充 OpenAPI 示例、错误码、鉴权、日志 trace id 和测试。流式 API 还要冻结事件 Schema 和结束语义。

## 6. 新增知识类型

不要把所有内容都扔进一个 `biz` Collection。先定义：

- 文档所属租户/团队。
- 知识类型：SOP、服务目录、事故复盘、用户记忆。
- 版本、有效期和权限。
- 删除与重建策略。
- 查询时 Metadata Filter。

长期记忆和公共知识库可以共享 Milvus 基础设施，但应使用不同 Collection 或严格 namespace/filter。

## 7. 将同步工作改为后台任务

上传接口目前同步 Embedding。文件大或并发高时可改为：

```text
POST /upload → 保存文件 → 创建 job → 202 Accepted
Worker → 分块/Embedding/写入
GET /jobs/{id} → pending/running/succeeded/failed
```

需要处理重复任务、取消、重试、进度、失败清理和索引版本切换。

## 8. 扩展完成定义

一个能力只有同时满足以下条件才算完成：

- 正常路径可运行。
- 失败路径可解释。
- 状态和权限隔离正确。
- 有结构化日志和指标。
- 有确定性单元测试及最小端到端测试。
- 文档说明输入、输出、限制和回滚方式。

