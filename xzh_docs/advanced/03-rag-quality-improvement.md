# 二次开发 03：RAG 质量提升

## 基线先行

先建立 30～100 个真实问题，标注期望来源、关键事实和拒答条件。没有基线，修改 Chunk 或 Top K 只能凭主观感受。

## 推荐检索管线

```text
原始问题
→ 查询分类/改写
→ tenant + document type + validity 过滤
→ 向量召回 + 关键词召回
→ 合并去重
→ Reranker
→ 相似度/相关性阈值
→ 上下文压缩
→ 带引用生成
```

## 优先改造

1. 给文档增加 tenant、版本、有效期和知识类型 metadata。
2. 保存检索结果和 score，支持调试。
3. 混合 BM25/关键词与向量召回，弥补专有名词和错误码检索。
4. 使用 Reranker 对候选 Chunk 二次排序。
5. 为答案附 source/chunk id，而非只给文件名。
6. 无结果或低于阈值时明确拒答。
7. 对长文档使用父子 Chunk：小块召回，大块给模型。

## 索引版本

记录 splitter、embedding model、dimension 和 schema 版本。重建时写入新 Collection，验证通过后原子切换别名，避免启动期间删除线上 Collection。

## 验收

对比改造前后的 Recall@K、MRR、Faithfulness、平均上下文 Token、P95 延迟和单查询成本。任何质量提升都不能以跨租户召回为代价。

