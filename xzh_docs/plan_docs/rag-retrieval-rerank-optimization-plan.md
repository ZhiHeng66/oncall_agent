# RAG 召回优化与 Rerank 引入规划

## 1. 文档目的

本文规划企业知识服务多智能体协同平台下一阶段的 RAG 优化工作，范围严格限定为：

1. 优化 Knowledge Agent 的候选证据召回能力。
2. 在候选召回之后引入独立 Rerank 精排。

本阶段不修改文档切分策略、不更换 Embedding 模型、不调整回答 Prompt，也不扩展生成侧的事实校验能力。评估数据与测试仅作为证明本次召回和排序改造有效的必要条件，不作为额外产品功能。

## 2. 当前实现基线

当前知识检索链路如下：

```text
用户问题
  → text-embedding-v4（1024 维）
  → Milvus biz Collection
  → IVF_FLAT + L2 纯稠密向量检索
  → Top-3
  → 相关度阈值 0.3 过滤
  → Knowledge Agent 生成带引用的回答
```

对应当前实现：

- `app/agent/specialists/knowledge/retriever.py` 使用
  `similarity_search_with_relevance_scores()` 执行单路向量检索。
- `app/config.py` 中 `rag_top_k=3`，`knowledge_min_relevance_score=0.3`。
- `app/core/milvus_client.py` 中 Collection 只有一个 1024 维稠密向量字段，索引为
  `IVF_FLAT`，距离度量为 `L2`。
- `KnowledgeRecord` 目前只保存一个 `relevance_score`，无法区分稠密召回、关键词召回、
  融合排序与 Rerank 的不同分数。
- 当前没有 BM25/关键词检索、结果融合和独立 Rerank 模型。

## 3. 当前问题

### 3.1 纯向量召回对精确词不稳定

稠密向量适合语义相似问题，但错误码、产品型号、配置名、接口名、工单编号和命令等内容依赖精确词匹配。纯向量检索可能召回主题相近的片段，却漏掉包含精确标识符的片段。

### 3.2 Top-3 直接进入生成阶段，候选池过小

当前检索只取三个候选，没有“宽召回、窄精排”的过程。正确证据如果在初始向量结果的第四名以后，就不会再有机会进入生成上下文。

### 3.3 向量相似度不能替代问题—证据相关性判断

向量相似度衡量整体语义接近程度，但不一定能判断片段是否直接回答问题。主题相关但没有答案的片段可能排在真正包含答案的片段之前。

### 3.4 当前阈值不能直接复用于混合检索

现有 `0.3` 阈值对应 LangChain 对向量距离转换后的相关度。BM25、RRF 融合和 Rerank 分数具有不同含义，不能把同一个阈值直接套用到所有阶段。

## 4. 目标架构

优化后的链路规划如下：

```text
                        ┌→ 稠密向量召回 Top-N ─┐
用户问题 → 查询入口 ───┤                       ├→ RRF 融合与去重
                        └→ BM25 召回 Top-N ────┘
                                      ↓
                               候选集 Top-M
                                      ↓
                             qwen3-rerank 精排
                                      ↓
                          阈值过滤 + 最终 Top-K
                                      ↓
                         Knowledge Agent 生成回答
```

规划初始参数只作为可执行起点，不作为最终结论：

| 参数 | 初始值 | 说明 |
| --- | ---: | --- |
| Dense 候选数 | 20 | 稠密向量召回数量 |
| BM25 候选数 | 20 | 关键词召回数量 |
| 融合候选数 | 20 | 去重后交给 Rerank 的最大数量 |
| 最终证据数 | 3 | Rerank 后交给 Knowledge Agent 的数量 |
| 融合方式 | RRF | 不直接比较 L2 与 BM25 的原始分数 |
| Rerank 模型 | `qwen3-rerank` | 作为首个可配置实现，不写死在业务逻辑中 |

所有候选数、模型、超时和阈值都必须从 `app/config.py` 读取，并通过固定数据集确定最终值。

## 5. 优化一：Dense + BM25 混合召回

### 5.1 技术方案

采用 Milvus 原生混合检索：

- 保留 `text-embedding-v4` 生成的 1024 维 Dense Vector。
- 为原始 `content` 字段启用文本 Analyzer。
- 使用 Milvus BM25 Function 从原始文本生成 Sparse Vector。
- 分别对 Dense Vector 和 Sparse Vector 建立索引。
- 查询时同时执行 Dense 与 BM25 两路召回。
- 使用 RRF（Reciprocal Rank Fusion）按排名融合两路结果。

选择 Milvus 原生 BM25，而不是在应用进程内维护一个临时 BM25 索引，原因是：

- 文档与稠密、稀疏索引保存在同一数据系统中，更新边界更清晰。
- 多实例部署时不会出现每个进程持有不同关键词索引的问题。
- 不需要应用自行维护语料统计、词频状态和索引持久化。
- 仓库当前 PyMilvus 锁定为 2.6.9，Milvus 服务镜像为 2.5.10；Milvus 2.5 已支持
  BM25 Full-Text Search，但实施前必须先验证该客户端、服务端与当前
  `langchain-milvus` 组合下的 Schema 创建、写入和混合查询兼容性。

### 5.2 为什么使用 RRF 融合

当前 Dense 检索采用 L2，数值越小越相似；BM25 数值越大通常越相关。两个分数的范围和方向都不同，直接做 `0.6 × dense_score + 0.4 × bm25_score` 缺少可靠含义。

第一版采用 RRF，根据文档在两路结果中的排名进行融合，不直接依赖两类原始分数的绝对大小。后续只有在固定评估集证明加权融合更优时，才考虑 Weighted Ranker，并记录确定权重的实验结果。

### 5.3 Collection Schema 变更

Milvus BM25 Function、Analyzer 和 Sparse Vector 字段必须在创建 Collection 时定义，不能直接添加到当前已经存在的 `biz` Collection。因此本次不能原地修改旧 Collection。

规划创建一个新版本 Collection，例如：

```text
biz_rag_v2
```

建议字段如下：

| 字段 | 类型 | 用途 |
| --- | --- | --- |
| `id` | VARCHAR Primary Key | 沿用当前文档片段 ID |
| `content` | VARCHAR + Analyzer | 原文及 BM25 输入 |
| `dense_vector` | FLOAT_VECTOR(1024) | 语义召回 |
| `sparse_vector` | SPARSE_FLOAT_VECTOR | BM25 关键词召回 |
| `metadata` | JSON | 沿用当前来源元数据 |

索引初始设计：

- Dense：先沿用当前 `IVF_FLAT + L2`，避免在同一次实验中同时改变过多变量。
- Sparse：`SPARSE_INVERTED_INDEX + BM25`。
- Analyzer：先验证 Milvus Analyzer 对中文、英文、错误码和下划线标识符的分词结果；若默认 Analyzer 对中文不满足要求，再选择 Milvus 支持的中文 Analyzer 配置。Analyzer 必须在重建索引前确定。

### 5.4 索引迁移与回退

实施时采用新 Collection 重建，而不是删除或覆盖旧 `biz`：

1. 创建新 Collection Schema 和索引。
2. 使用现有上传文档重新生成 Dense Vector，并由 Milvus 生成 BM25 Sparse Vector。
3. 对新 Collection 运行固定召回评估。
4. 验证通过后通过配置或 Collection Alias 切换读流量。
5. 保留旧 Collection，直到回归和演示验证结束。

如果新链路失败，可以切回旧 Collection 和 Dense-only 模式。未经用户明确授权，不删除旧 Collection。

### 5.5 检索结果契约

扩展当前 `KnowledgeRecord`，让不同阶段的分数具有明确语义。建议内部契约至少包含：

```text
document
dense_score            可选
sparse_score           可选
fusion_score           可选
rerank_score           可选
retrieval_sources      dense / sparse / dense+sparse
```

这些字段用于测试、日志和安全 Trace。Knowledge Agent 对外仍接收标准化证据，不把供应商原始响应直接暴露给 API。

### 5.6 召回降级策略

混合召回属于只读能力，应保持明确且有限的降级：

- Dense 成功、BM25 失败：返回 Dense 候选，并标记 `dense_only`。
- BM25 成功、Dense 失败：返回 BM25 候选，并标记 `sparse_only`。
- 两路都失败：检索失败，Knowledge Agent 返回 `failed`。
- 两路都成功但没有候选：返回空召回，Knowledge Agent 返回 `partial`。

不能把两路都失败伪装成“知识库没有答案”。完整异常只写服务端日志，对 Agent 和 API 使用清洗后的结构化错误。

## 6. 优化二：引入独立 Rerank

### 6.1 Rerank 的职责

RRF 解决的是多路召回结果如何合并，独立 Reranker 解决的是“候选片段是否真正回答当前问题”。这两个步骤不能混为一谈。

Reranker 输入：

```text
query + 候选文档列表
```

Reranker 输出：

```text
候选文档索引 + rerank_score + 新排序
```

第一版计划使用阿里云百炼 `qwen3-rerank`，原因是项目已经使用 DashScope，能复用现有供应商配置与调用边界；该模型作为默认配置而不是固定实现，以便后续在不修改 Knowledge Agent 的情况下替换模型。

### 6.2 代码边界

新增独立 Reranker 协议，避免把供应商调用直接写进 Knowledge Agent：

```python
class KnowledgeReranker(Protocol):
    async def rerank(
        self,
        query: str,
        records: list[KnowledgeRecord],
        *,
        top_k: int,
    ) -> list[KnowledgeRecord]: ...
```

建议模块：

```text
app/agent/specialists/knowledge/reranker.py
```

其中包含：

- `KnowledgeReranker`：协议边界。
- `DashScopeKnowledgeReranker`：真实远程实现。
- 测试中通过 Fake Reranker 注入，不依赖真实 API Key。

Reranker 只负责排序和分数标准化，不负责生成答案，也不改变 Chunk 内容和来源元数据。

### 6.3 超时、重试与降级

Rerank 是外部模型调用，必须设置硬限制：

- 独立超时，建议初始值 10 秒。
- 只对明确的瞬时网络错误和限流进行有限重试，建议最多 1 次额外重试。
- 不对参数错误、鉴权错误和确定性 4xx 反复重试。
- 返回文档索引越界、重复或缺失时视为无效供应商响应。

Rerank 失败时不应让整个知识问答不可用：

```text
混合召回成功 + Rerank 失败
→ 使用 RRF 融合后的顺序返回最终 Top-K
→ 记录 rerank_degraded=true 和清洗后的错误
```

该降级是“使用已经成功获得的候选”，不是伪造 Rerank 成功。

### 6.4 分数与阈值

不得继续使用 `knowledge_min_relevance_score=0.3` 同时过滤向量分数、融合分数和 Rerank 分数。

规划拆分为：

- Dense 原始分数：只保留用于诊断，不直接作为混合链路的最终阈值。
- BM25 原始分数：只保留用于诊断。
- RRF 融合分数：用于候选排序，不与 Rerank 分数混用。
- Rerank 分数：使用独立 `rag_rerank_min_score`，其值必须通过评估集校准。

如果 Rerank 模型的分数未经过可靠校准，第一阶段可以先按排序取 Top-K，同时记录分数分布；确认正负样本分布后再启用最低分阈值。

## 7. 配置规划

在 `app/config.py` 中增加类型化配置，建议如下：

```text
RAG_RETRIEVAL_MODE=hybrid          # dense | hybrid
RAG_DENSE_CANDIDATE_K=20
RAG_SPARSE_CANDIDATE_K=20
RAG_FUSION_CANDIDATE_K=20
RAG_FINAL_TOP_K=3
RAG_FUSION_STRATEGY=rrf
RAG_RRF_K=60

RAG_RERANK_ENABLED=true
RAG_RERANK_MODEL=qwen3-rerank
RAG_RERANK_TIMEOUT_SECONDS=10
RAG_RERANK_MAX_RETRIES=1
RAG_RERANK_MIN_SCORE=              # 评估完成前不启用固定值

MILVUS_RAG_COLLECTION=biz_rag_v2
```

兼容策略：

- 保留原 `rag_top_k` 一段迁移期，用于 Dense-only 兼容路径。
- 新混合链路明确区分候选数量和最终证据数量。
- `.env.example` 只增加占位符和说明，不写入真实 Key。
- Rerank 默认复用 `DASHSCOPE_API_KEY`，不增加重复凭据。

## 8. 计划修改范围

预计修改或新增以下模块，最终以实施时源码为准：

| 文件 | 计划改动 |
| --- | --- |
| `app/config.py` | 增加混合召回、候选数、Rerank、Collection 配置与校验 |
| `.env.example` | 增加非敏感配置示例 |
| `app/core/milvus_client.py` | 支持新 Collection 的 Dense、Sparse、Analyzer、BM25 Function 和索引 |
| `app/services/vector_store_manager.py` | 支持选择新 Collection 并写入混合检索所需字段 |
| `app/agent/specialists/knowledge/schemas.py` | 扩展候选记录的分阶段分数与召回来源 |
| `app/agent/specialists/knowledge/retriever.py` | 从单路 Dense 检索改为可配置 Dense/Hybrid Retriever |
| `app/agent/specialists/knowledge/reranker.py` | 新增 Reranker 协议与 DashScope 实现 |
| `app/agent/specialists/knowledge/agent.py` | 串联召回、Rerank、降级和最终证据选择 |
| `tests/unit/` | 增加融合、去重、分数契约、Rerank 与降级测试 |
| `tests/integration/` | 增加 Milvus 混合检索集成测试，外部服务必须可替换 |
| `tests/evals/` | 增加真实检索排序对比用的固定小型数据集和评估入口 |
| `README.md` 或相关架构文档 | 仅在实现完成后更新真实能力与运行方式 |

不计划修改：

- `document_splitter_service.py` 的 Chunk 策略。
- `vector_embedding_service.py` 的模型和 1024 维配置。
- Knowledge Prompt 和最终回答格式。
- Supervisor、Data Agent、Task Agent 和 Reviewer 的职责。
- 现有 API 与 SSE 公共契约。

## 9. 分阶段实施计划

### 阶段 0：建立当前检索基线

1. 从项目知识文档中准备固定问题和标准相关 Chunk。
2. 样本至少覆盖语义改写、精确错误码、英文配置名、中文制度术语和相似干扰文档。
3. 运行当前 Dense Top-3/Top-5/Top-20，记录 Recall@K、MRR 和查询耗时。
4. 基线结果存入可复现报告，不在没有运行证据时填写提升比例。

完成条件：相同数据、相同命令能够重复得到当前 Dense-only 基线。

### 阶段 1：建立检索契约与配置

1. 使用最小实验 Collection 验证 PyMilvus 2.6.9、Milvus 2.5.10 和当前
   `langchain-milvus` 对 Analyzer、BM25 Function、Sparse Index 与 Hybrid Search 的兼容性。
2. 扩展 `KnowledgeRecord` 的分阶段分数字段。
3. 引入 Retrieval Mode、各阶段候选数和 Collection 配置。
4. 保留 Dense-only 实现作为兼容与回退路径。
5. 为配置范围和不变量增加单元测试，例如最终 Top-K 不得大于融合候选数。

完成条件：不连接真实 Milvus 时，可以通过 Fake 验证 Dense-only 和 Hybrid 两种模式的选择。

### 阶段 2：创建混合索引并实现双路召回

1. 创建 `biz_rag_v2` Schema。
2. 为 `content` 配置 Analyzer 和 BM25 Function。
3. 建立 Dense 与 Sparse 索引。
4. 实现 Dense + BM25 双路查询。
5. 使用 RRF 融合，并按主键去重。
6. 保留两路原始分数、排名和召回来源。
7. 完成单路失败降级与双路失败处理。

完成条件：精确错误码样本能够由 BM25 命中，语义改写样本能够由 Dense 命中，融合结果不存在重复 Chunk。

### 阶段 3：引入 Rerank

1. 定义 `KnowledgeReranker` 协议。
2. 实现 `DashScopeKnowledgeReranker`，默认模型配置为 `qwen3-rerank`。
3. 将融合后的候选批量提交给 Reranker，禁止逐条调用。
4. 验证供应商返回的索引、数量和分数。
5. 选择最终 Top-K，并保留原始来源元数据。
6. 实现超时、有限重试和 RRF 顺序降级。

完成条件：Fake Reranker 可以稳定改变候选排序；超时、无效响应和供应商失败时仍能返回融合候选，并明确记录降级。

### 阶段 4：离线对比与参数确定

对同一数据集运行：

1. Dense-only。
2. Dense + BM25 + RRF。
3. Dense + BM25 + RRF + Rerank。

重点比较：

- Recall@3、Recall@5、Recall@20。
- MRR 或 NDCG。
- 精确标识符问题的命中率。
- Rerank 前后正确证据的平均排名。
- 空结果和错误证据进入最终 Top-K 的比例。
- 平均与 P95 检索延迟。
- 单次 Rerank 调用文档数及调用成本。

只有第三种方案在目标数据集上取得可复现的质量收益，且延迟处于可接受范围，才默认开启 Hybrid 和 Rerank。

### 阶段 5：兼容上线与回退验证

1. 先完成新 Collection 全量重建。
2. 在测试环境开启 Hybrid，不影响旧 Collection。
3. 验证 API 和 SSE 契约不变。
4. 验证 Rerank 不可用时的降级。
5. 通过配置切换到新 Collection。
6. 保留 `dense` 模式与旧 Collection 作为回退选项。

完成条件：切换与回退不要求重新修改业务代码，且旧知识问答接口保持兼容。

## 10. 测试计划

### 10.1 单元测试

- Dense、Sparse 候选按主键正确去重。
- RRF 融合不依赖 L2 与 BM25 分数的绝对值。
- 同一 Chunk 被两路召回时保留两种来源。
- 配置的候选数和最终 Top-K 得到正确应用。
- Reranker 正确按照返回索引重新排序。
- Reranker 返回重复索引、越界索引、缺失结果时被识别为无效响应。
- Rerank 超时或失败时降级到 RRF 顺序。
- 单路召回失败时使用另一条成功链路。
- 双路失败与空召回被区分。
- 对外 Evidence 保留原文和来源元数据，不暴露原始供应商响应。

### 10.2 集成测试

- 新 Collection Schema、Analyzer、BM25 Function 和两个索引可成功创建。
- 同一批文档能够同时进行 Dense 和 BM25 检索。
- 混合检索返回稳定的文档标识和内容。
- 重新索引后旧文档不会在新 Collection 中重复出现。
- Knowledge Agent 能消费 Rerank 后的记录并生成既有 Evidence 契约。

普通测试必须使用 Fake，不依赖真实 DashScope Key 或在线 Milvus。真实 Milvus 和真实 Rerank 调用应使用明确标记的集成或评估命令。

### 10.3 回归测试

- 现有 Knowledge Agent 的证据不足、检索失败和生成失败行为保持不变。
- Supervisor 路由和其他 Specialist 不受影响。
- `/api/chat`、`/api/chat_stream`、`/api/agent/run` 和 `/api/agent/run_stream` 的现有契约不因内部检索调整而改变。

## 11. 验收标准

### 11.1 功能验收

- 支持配置选择 `dense` 或 `hybrid` 召回。
- Hybrid 模式确实执行 Dense 与 BM25 两路检索并完成去重融合。
- Rerank 可独立开启和关闭。
- Rerank 失败时能够透明降级到 RRF 结果。
- Knowledge Agent 最终只接收配置数量的高排名证据。
- 每个候选能够追踪来自 Dense、BM25 还是两者共同召回。

### 11.2 质量验收

- 在固定数据集上，Hybrid 的 Recall@K 不低于 Dense-only 基线。
- 精确错误码、接口名和产品标识符类问题的召回结果应有明确改善。
- 引入 Rerank 后，正确证据的 MRR/NDCG 或平均排名应优于仅 RRF 融合。
- 不使用没有数据支撑的提升百分比；最终结果以评估报告为准。

### 11.3 工程验收

- 所有新配置有类型、范围校验和 `.env.example` 说明。
- 外部 Rerank 调用具有超时、有限重试、错误清洗和降级。
- 普通测试不依赖真实外部服务。
- 运行最小相关测试后，再运行所有可行的 `make lint`、`make type-check` 和测试命令。
- 文档只在能力完成并通过验证后，才把 Hybrid/Rerank 描述为已实现。

## 12. 风险与控制

| 风险 | 影响 | 控制措施 |
| --- | --- | --- |
| 中文 Analyzer 分词效果不理想 | BM25 无法稳定命中中文术语 | 先做分词样例验证，再确定 Analyzer 并重建索引 |
| PyMilvus、Milvus 服务端与 LangChain-Milvus 版本组合不兼容 | 无法创建或查询混合索引 | 阶段 1 先做最小兼容性实验；不通过时停止迁移并重新确定版本组合 |
| 新旧 Collection Schema 不兼容 | 不能原地升级 | 使用新 Collection 重建并保留旧 Collection 回退 |
| 候选数过大 | 延迟和 Rerank 成本增加 | 候选数配置化，通过延迟与质量联合评估 |
| Rerank 服务超时或限流 | 知识问答延迟或失败 | 独立超时、有限重试、降级到 RRF 顺序 |
| 混用不同分数阈值 | 错误过滤正确证据 | 分阶段保存分数，阈值分别校准 |
| Hybrid 只提升召回但引入噪声 | 最终答案质量下降 | 使用 Rerank，并同时评估最终 Top-K 精度 |
| Rerank 对候选内容截断 | 关键答案未参与排序 | 控制候选片段长度并记录请求 Token；本阶段不改 Chunk 策略 |

## 13. 明确不在本阶段实施的内容

为避免范围扩张，本计划不包含：

- 修改 Chunk 大小、Overlap 或标题切分规则。
- 更换 `text-embedding-v4` 或调整 1024 维向量。
- 父子 Chunk、上下文压缩和多轮 Query Rewrite。
- 修改 Knowledge Prompt、引用格式或 Reviewer 规则。
- 引入新的向量数据库。
- 修改 Supervisor-Specialist 编排结构。
- 对外 API 或 SSE 协议升级。

如果后续评估表明召回瓶颈主要来自 Chunk，而不是检索和排序，本阶段应记录结论，但不顺手扩大修改范围；Chunk 优化应单独立项。

## 14. 实施决策摘要

本次优化采用以下主路线：

```text
Milvus Dense + 原生 BM25
→ RRF 融合
→ qwen3-rerank 精排
→ 最终 Top-3
```

其中：

- Dense 负责语义召回。
- BM25 负责错误码、接口名和产品标识等精确词召回。
- RRF 负责融合不可直接比较的两路排名。
- `qwen3-rerank` 负责判断候选是否真正回答问题。
- 旧 Dense-only 链路保留为兼容和回退路径。
- 最终参数与是否默认开启必须由固定评估集决定。
