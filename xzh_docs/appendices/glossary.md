# 术语表

| 术语 | 本项目中的含义 |
|---|---|
| Agent | 能让模型根据状态选择工具并多步执行的运行系统，不只是一个 Prompt |
| Tool Calling | 模型输出结构化工具名和参数，由宿主程序真正执行函数 |
| Runnable | LangChain 中具有 `invoke/ainvoke/stream/astream` 等统一调用接口的组件 |
| Structured Output | 让模型结果解析为 Pydantic 模型等结构，而非自由文本 |
| State | LangGraph 各节点共享并逐步更新的数据 |
| Reducer | 合并节点状态更新的规则，例如 `operator.add` 追加历史 |
| Node | 状态图中的可执行函数或 Runnable |
| Conditional Edge | 根据当前 State 决定下一节点的边 |
| Checkpoint | 某一步图状态的快照，用于会话、恢复和中断 |
| thread_id | Checkpointer 区分不同执行线程/会话的键 |
| 短期记忆 | 当前会话消息和工作流状态 |
| 长期记忆 | 跨会话保存并按需取回的用户事实、偏好或历史经验 |
| RAG | 在生成前检索外部知识并将证据提供给模型 |
| Embedding | 把文本映射为数值向量，使语义相近文本在向量空间中接近 |
| Chunk | 文档切分后的检索单元 |
| VectorStore | LangChain 对向量数据库操作的统一抽象 |
| Retriever | 接受查询并返回相关 Document 的检索接口 |
| MCP | 让模型应用以统一协议发现和调用外部工具/资源的协议 |
| Transport | MCP 消息的传输方式，例如 stdio、SSE、Streamable HTTP |
| SSE | 服务端通过一个 HTTP 长连接持续向客户端推送文本事件 |
| ReAct | 模型在推理/行动/观察之间循环的 Agent 模式 |
| Plan–Execute–Replan | 先规划、逐步执行，再根据结果决定继续、改计划或回答 |
| Hallucination | 模型生成了没有证据或与事实不符的信息 |
| Grounding | 用工具结果、检索文档等可验证证据约束回答 |
| Idempotency | 同一操作重复执行不会造成额外副作用 |

