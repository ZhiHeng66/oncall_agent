# KnowledgeHub Enterprise 产品手册

KnowledgeHub Enterprise 是企业内部知识检索与问答产品。标准版提供文档上传、切分、向量检索和引用展示；协同版增加 Supervisor 路由、Knowledge/Data/Task Specialist 与 Reviewer 质量检查。

管理员可上传 Markdown、PDF、DOCX 和 TXT 文件。单个文件必须满足服务端扩展名、大小和解析路径校验。知识问答返回的引用来自已索引文档；未检索到证据时，系统应明确提示证据不足。

统一 Agent 接口为 `/api/agent/run` 与 `/api/agent/run_stream`。历史 `/api/chat`、`/api/chat_stream` 和 `/api/aiops` 在迁移期保留兼容。

