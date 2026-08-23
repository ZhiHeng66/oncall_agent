# 第 3 天：RAG、Embedding 与 Milvus

## 今日目标

完成后你应能分别解释索引链路和查询链路，知道 Chunk、Embedding、向量距离和 Top K 如何共同影响答案，并能通过检索结果而不是最终文案判断 RAG 是否正常。

## 1. RAG 的位置

LLM 参数中的知识有截止时间，且无法可靠掌握企业私有文档。RAG 在生成前取回相关证据：

```text
问题 → 检索相关文档 → 把文档交给模型 → 基于证据回答
```

RAG 不会自动保证答案正确。它有两个独立阶段：

- Retrieval：能否找回正确证据？
- Generation：模型是否忠实使用证据？

调试时必须分开评估。

## 2. 写入链路

```text
UploadFile
→ 验证扩展名与大小
→ 保存 uploads/<safe_filename>
→ 读取 UTF-8 文本
→ 删除相同 source 的旧 Chunk
→ 文档切分
→ 为每个 Chunk 生成 1024 维向量
→ 写入 biz Collection
```

`/api/upload` 即使索引失败也仍可能返回上传成功，只在日志中记录索引错误。因此“文件存在”不代表“知识可检索”。生产接口应明确返回索引状态或使用异步任务状态。

## 3. 文档分块

检索的最小单位不是整篇文件，而是 `Document(page_content, metadata)`。

Markdown 使用两阶段切分：

1. `MarkdownHeaderTextSplitter` 按一级、二级标题分段，并保留标题。
2. `RecursiveCharacterTextSplitter` 对过长段落继续切分。
3. `_merge_small_chunks` 尝试合并小于 300 字符的片段。

配置中的 `chunk_max_size=800` 在代码中被乘以 2 后作为递归切分大小，因此实际二次切分上限目标约为 1600 字符，并非直觉上的 800。文档应以运行值为准。

### Chunk 的权衡

- 太小：语义不完整，召回后缺上下文，分片数量和成本上升。
- 太大：主题混杂，向量表达变模糊，占用 Prompt 空间。
- overlap 太小：跨边界信息断裂。
- overlap 太大：重复召回、存储和 Token 浪费。

最佳值依赖文档结构和真实查询，不能靠固定“行业标准”。

## 4. Metadata 为什么重要

项目给 Chunk 添加：

```text
_source      绝对/规范化文件路径
_extension   文件扩展名
_file_name   文件名
h1/h2        Markdown 标题层级
```

用途包括：显示引用、按来源删除、Metadata Filter、调试命中位置。Metadata 是 RAG 可维护性的基础，不能只存正文和向量。

当前删除表达式直接插入文件路径：

```python
expr = f'metadata["_source"] == "{file_path}"'
```

二次开发时需考虑引号转义、统一路径规范和租户过滤。

## 5. Embedding 的直观理解

Embedding 模型把字符串映射为固定长度向量：

```text
"CPU 使用率很高" → [0.021, -0.18, ..., 0.07]
```

向量不是人类可读标签。其价值在于：语义相近的文本通常在该模型的向量空间中距离较近。文档和查询必须使用兼容的模型、维度和预处理。

当前实现：

- 模型：`text-embedding-v4`
- 维度：1024
- API：DashScope OpenAI 兼容接口
- 文档：`embed_documents` 批量调用
- 查询：`embed_query` 单条调用

如果 Collection 是旧维度，`MilvusClientManager.connect()` 会删除并重建 Collection。这是破坏性行为，生产环境不应在启动时静默重建，应使用版本化迁移。

## 6. Milvus Collection

`biz` Collection 的主要字段：

| 字段 | 类型 | 用途 |
|---|---|---|
| `id` | VARCHAR 主键 | Chunk 唯一 ID |
| `vector` | FLOAT_VECTOR(1024) | 语义向量 |
| `content` | VARCHAR | Chunk 正文 |
| `metadata` | JSON | 来源和标题等信息 |

索引配置使用 IVF_FLAT 和 L2：

```text
metric_type = L2
index_type  = IVF_FLAT
nlist       = 128
nprobe      = 10（直接搜索服务中）
```

L2 是距离，通常越小越相似；不要把它误当“越大越好”的相似度分数。不同封装的 `similarity_search_with_score` 对 score 语义可能不同，使用前必须验证。

## 7. VectorStore 与原生 SDK

项目有两层 Milvus 使用方式：

1. `langchain_milvus.Milvus`：主 RAG 路径，提供 `add_documents`、`as_retriever` 等统一接口。
2. `pymilvus.Collection`：创建 Schema、删除、直接搜索、健康检查。

`VectorStoreManager` 同时依赖 Embedding，因此：

```python
vector_store.add_documents(documents)
```

内部会先调用 `embed_documents`，再写数据库。Retriever 在查询时会调用 `embed_query`。

## 8. 查询链路

`retrieve_knowledge` 是 LangChain Tool：

```text
query
→ vector_store.as_retriever(k=rag_top_k)
→ retriever.invoke(query)
→ List[Document]
→ format_docs
→ (上下文文本, 原始 Document 列表)
```

它设置 `response_format="content_and_artifact"`：模型主要消费格式化文本，原始 Document 可以作为 artifact 保留给运行时或 UI。直接 `tool.ainvoke()` 时，应通过实验验证版本下实际返回的内容形态；Planner 注释明确认为其得到字符串。

### 两种触发方式

- 对话 Agent：工具被注册，由模型判断是否检索。
- AIOps Planner：代码显式调用检索，制定计划前必查一次经验。

因此“普通聊天没命中文档”可能不是向量检索坏了，而是模型根本没选择该工具。排查时先看 tool call。

## 9. RAG 的质量指标

建议建立问题—证据数据集：

```json
{
  "question": "磁盘占用过高如何处理？",
  "expected_sources": ["disk_high_usage.md"],
  "expected_facts": ["检查大文件", "清理日志"],
  "must_not_claim": ["已经自动删除文件"]
}
```

分别统计：

- Recall@K：正确文档是否出现在前 K 个结果。
- MRR：正确文档的排序是否靠前。
- Faithfulness：回答是否都能由证据支持。
- Citation correctness：引用是否真的支持相邻结论。
- No-answer accuracy：没有资料时是否诚实拒答。

## 10. 今日实验

### 实验 A：查看 Chunk

读取 `aiops-docs/disk_high_usage.md`，调用 `split_document`，打印每个 Chunk 的长度、标题 metadata 和前 100 个字符。记录是否出现断句和过度合并。

### 实验 B：验证覆盖更新

上传同一文件两次，比较删除日志和 Collection 实体数量，确认没有无界重复。再修改文件内容并上传，搜索旧句子和新句子。

### 实验 C：分离检索与生成

直接调用 `retrieve_knowledge.invoke({"query": ...})`，不经过 Agent。先判断召回是否正确，再调用对话接口。这样能定位问题发生在 Retrieval 还是 Generation。

### 实验 D：参数对比

对同一组 10 个问题分别测试 `top_k=1/3/5`，记录正确来源排名和提供给模型的总字符数。再选一篇文档对比不同 chunk size。

## 11. 今日自测

1. 为什么上传成功不等于索引成功？
2. 当前实际递归切分大小为何不是配置中的 800？
3. Metadata 除了显示文件名还能做什么？
4. L2 score 应按越大还是越小理解？
5. 普通对话没有使用知识库有哪些可能原因？
6. 如何证明问题发生在召回而不是生成？

能够从一篇原始 Markdown 追踪到 Milvus Chunk，再直接检索回正确来源，即完成第 3 天。

