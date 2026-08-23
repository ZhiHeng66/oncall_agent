# 二次开发 09：多用户、多租户与权限

## 隔离层级

```text
tenant
└── user/team
    ├── threads/checkpoints
    ├── long-term memories
    ├── documents/vector chunks
    ├── tool permissions
    └── traces/evaluations
```

所有查询都必须从服务端认证上下文取得 tenant，不能信任请求 JSON 中的 tenant id。

## RAG 隔离

每个 Chunk 必须包含 tenant 和访问范围。检索时先强制 metadata filter，再做 Top K。公共文档可使用明确的 public namespace，不能通过“检索后再过滤”防泄漏。

## Checkpoint 隔离

thread 表记录 owner/tenant。读取、追加、清除和恢复都验证归属。外部 session id 可作为显示值，但内部使用不可预测的 UUID。

## Tool 权限

权限在 Registry/Policy 层过滤。模型看不到无权使用的工具，高风险工具还需运行时二次授权。MCP 凭证按租户或服务账号管理，禁止所有用户共享全权限密钥。

## 配额

按 tenant/user 限制请求、并发、Token、索引容量、文件大小、长期记忆数量和工具调用。配额拒绝应可解释，并记录审计。

