# API 与流式事件速查

## 核心接口

| 方法 | 路径 | 请求 | 返回 |
|---|---|---|---|
| GET | `/` | 无 | 静态首页或应用信息 |
| GET | `/health` | 无 | 应用及 Milvus 健康状态；异常时 HTTP 503 |
| POST | `/api/chat` | `{"Id":"...","Question":"..."}` | 完整 JSON 回答 |
| POST | `/api/chat_stream` | 同上 | SSE |
| POST | `/api/chat/clear` | `{"sessionId":"..."}` | 清除结果 |
| GET | `/api/chat/session/{id}` | path id | 内存会话历史 |
| POST | `/api/aiops` | `{"session_id":"..."}` | AIOps SSE |
| POST | `/api/upload` | multipart file | 保存/索引信息 |
| POST | `/api/index_directory` | query `directory_path` | 批量索引结果 |

当前健康检查没有 `/api` 前缀，精确路径是 `/health`。主项目 README 中出现过 `/api/health` 的旧说明，调试时以 `app/api/health.py`、路由注册方式和 `/docs` 为准。

## 对话 SSE

每个事件的 `event` 为 `message`，`data` 是 JSON 字符串：

```json
{"type":"content","data":"文本增量"}
{"type":"done","data":null}
{"type":"error","data":"错误信息"}
```

API 层还兼容 `tool_call/search_results/debug`，但当前 Service 不保证都会产出。

## AIOps SSE

主要类型：`plan`、`step_complete`、重规划/状态事件、`complete`、`error`。字段来自 `AIOpsService._format_*_event`。消费方对未知事件应忽略或通用展示，不能因此断开连接。

## 错误语义

当前 `/api/chat` 可能在 HTTP 成功响应中返回业务 `code: 500`；上传可能文件保存成功但索引失败。调用方不能只检查 HTTP 200。建议后续统一 HTTP 状态、业务错误码和异步任务状态。
