# 第 2 天：LangChain 与工具型 Agent

## 今日目标

今天把“模型会调用工具”拆回最基本的消息交换。完成后你应理解 `create_agent()` 替你循环了什么，并能新增一个本地工具。

## 1. LLM、Chat Model 和 Agent 的区别

```text
LLM/Chat Model：输入消息，输出一条模型消息
Tool：宿主程序可执行的函数及其 Schema
Agent：反复让模型决策、执行工具、把结果交回模型，直到回答
```

模型本身不会运行 Python。所谓 Tool Calling 是模型生成结构化意图：

```json
{
  "name": "get_current_time",
  "args": {"timezone": "Asia/Shanghai"}
}
```

真正找到函数、校验参数并执行的是 LangChain/LangGraph 运行时。

## 2. 消息是核心数据结构

主要消息类型：

| 类型 | 谁产生 | 用途 |
|---|---|---|
| `SystemMessage` | 应用 | 定义角色、边界、输出要求 |
| `HumanMessage` | 用户 | 提供问题或任务 |
| `AIMessage` | 模型 | 文本回答或工具调用请求 |
| `ToolMessage` | 工具运行时 | 把工具结果关联回对应调用 |
| `AIMessageChunk` | 流式模型 | 模型输出增量 |

工具调用的最小对话序列：

```text
SystemMessage
HumanMessage("现在几点？")
AIMessage(tool_calls=[get_current_time(...)])
ToolMessage("2026-08-19 10:00:00")
AIMessage("现在是……")
```

如果缺少 ToolMessage 或 call id 对不上，供应商通常会拒绝下一次模型请求。

## 3. `@tool` 做了什么

`app/tools/time_tool.py`：

```python
@tool
def get_current_time(timezone: str = "Asia/Shanghai") -> str:
    """获取当前时间……"""
```

装饰器把函数转成 LangChain Tool，至少包含：

- 名称：默认是函数名。
- 描述：主要来自 docstring，直接影响模型选择。
- 参数 Schema：来自类型标注和默认值。
- 执行入口：`invoke/ainvoke`。

工具描述不是普通注释，它是给模型看的产品接口。描述应说明“何时使用”，参数要明确单位、格式和限制，返回值应稳定且尽量结构化。

## 4. 项目的工具集合

`DEFAULT_LOCAL_AGENT_TOOLS` 当前包含：

```python
(
    retrieve_knowledge,
    get_current_time,
    query_prometheus_alerts,
)
```

Agent 异步初始化时，再把 `MultiServerMCPClient.get_tools()` 得到的远程工具追加进来，最后统一交给 `create_agent()`。对模型来说，本地和远程 Tool 具有相同的名称、描述和参数 Schema。

## 5. Runnable 与 LCEL

LangChain 组件通常暴露统一方法：

- `invoke`：同步单次调用。
- `ainvoke`：异步单次调用。
- `stream`：同步流式。
- `astream`：异步流式。

Planner 中：

```python
planner_chain = planner_prompt | llm.with_structured_output(Plan)
```

管道符表示把前一组件的输出传给后一组件，常被称为 LCEL 组合。输入字典先填充 Prompt，再交给模型，最后解析成 `Plan`。

## 6. Structured Output

自由文本计划难以被程序可靠消费，因此定义：

```python
class Plan(BaseModel):
    steps: list[str]
```

`with_structured_output(Plan)` 要求模型按 Schema 输出并解析为 Pydantic 对象。它解决“程序需要确定结构”的问题，但不是绝对保证：供应商兼容性、模型能力、Schema 复杂度都可能导致解析失败，因此生产代码仍需捕获错误和设置回退。

适合结构化输出的场景：计划、分类、路由决策、工具参数、证据列表。不适合强行把长篇最终报告拆成过度复杂的 Schema。

## 7. `create_agent()` 的概念循环

`RagAgentService._initialize_agent()`：

```python
self.agent = create_agent(
    self.model,
    tools=all_tools,
    checkpointer=self.checkpointer,
)
```

可把它理解为：

```text
输入 messages
    ↓
调用模型
    ↓
是否包含 tool_calls？
 ├── 否 → 返回最终消息
 └── 是 → 执行工具 → 追加 ToolMessage → 再调模型
                                      ↑          │
                                      └──────────┘
```

框架还负责消息合并、状态传递、流式事件和 Checkpoint。理解封装前，最好手写一次上述循环。

## 8. 系统提示与工具描述的分工

当前系统提示只定义通用工作原则，不列出工具。工具的 Schema 会由框架作为模型请求的一部分传入。

- System Prompt：身份、边界、总体决策和输出要求。
- Tool Description：工具的适用场景、参数和语义。
- User Message：当前目标。
- Runtime State：历史消息和工具观察。

不要在多个位置维护相互冲突的工具清单。

## 9. 非流式调用

`query()` 构造系统和用户消息，并使用 `thread_id=session_id` 调用 `ainvoke`。返回结果是状态字典，`messages` 包含整个执行后的消息序列，代码取最后一条作为答案。

值得验证：同一 `thread_id` 连续请求时，Checkpointer 会合并历史；每次输入又包含一条新的 SystemMessage。要通过实际 Checkpoint 检查系统消息是否重复、消息数量怎样增长，而不是凭想象判断。

## 10. 流式调用

```python
async for token, metadata in self.agent.astream(
    input=agent_input,
    config=config_dict,
    stream_mode="messages",
):
```

`messages` 模式面向模型消息增量。代码同时兼容 `content_blocks` 和 `content`，原因是模型集成和版本可能使用不同字段。`metadata['langgraph_node']` 可帮助判断 chunk 来自哪个节点。

另一个常用模式是 `updates`，返回每个节点对状态的更新；AIOps 正在使用它。两者选择取决于 UI 想显示文本 Token 还是工作流进度。

## 11. 会话记忆与消息裁剪

`MemorySaver` 使用 `thread_id` 保存图状态，因此同一进程内能恢复会话。但它具有明确边界：

- 不是持久化数据库。
- 不是跨会话语义记忆。
- 不会自动决定哪些用户事实值得长期保存。
- 多 Worker 不能天然共享。

项目定义了 `trim_messages_middleware()`，但当前没有接入 `create_agent()`。因此不能认为只保留最近三轮。长期运行时需处理上下文增长、系统消息重复、工具消息成对保留等问题。

## 12. 错误边界

工具型 Agent 至少有四类错误：

1. 模型请求错误：鉴权、限流、超时、内容格式。
2. 工具选择错误：模型选错工具或不调用工具。
3. 工具执行错误：远端不可用、参数不合法、业务失败。
4. 状态错误：消息序列不合法、thread id 冲突、序列化失败。

“HTTP 200 但答案不对”通常属于第 2 类，不能靠捕获异常解决，需要工具描述、Prompt 和评测集。

## 13. 今日实验

### 实验 A：检查工具 Schema

在 Python Shell 中打印：

```python
from app.tools import get_current_time

print(get_current_time.name)
print(get_current_time.description)
print(get_current_time.args_schema.model_json_schema())
```

### 实验 B：新增本地工具

实现一个只读工具 `get_service_owner(service_name: str)`，返回固定测试数据。要求：

- 明确“何时使用”的 docstring。
- 参数不能为空。
- 返回结构稳定。
- 注册进 `DEFAULT_LOCAL_AGENT_TOOLS`。
- 用问题验证模型是否选择它。

### 实验 C：手写工具循环

不用 `create_agent()`，使用 `bind_tools()` 调模型；若有 `tool_calls`，手工执行对应 Tool，构造消息后再次调用模型。比较它与 Executor 的实现。

### 实验 D：观察 Checkpoint

同一 session 连续问两个有关联的问题，再读取 `checkpointer.get(config)`，打印消息类型和摘要。重启服务后再次读取，验证数据是否仍在。

## 14. 今日自测

1. 模型是否真的执行 Python 工具？
2. Tool docstring 为什么会影响准确率？
3. `AIMessage(tool_calls=...)` 后为什么必须有 ToolMessage？
4. `prompt | llm.with_structured_output(Plan)` 的数据怎样流动？
5. `stream_mode="messages"` 与 `"updates"` 分别适合什么 UI？
6. Checkpoint 和长期记忆有什么区别？

能新增工具并从日志证明模型正确调用它，即完成第 2 天。

