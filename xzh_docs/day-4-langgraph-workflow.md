# 第 4 天：LangGraph 状态图与会话状态

## 今日目标

完成后你应能读懂 `StateGraph`，解释节点怎样更新 State、条件边怎样路由，并能区分消息流、状态更新流、Checkpoint 和长期记忆。

## 1. 为什么需要图

简单工具 Agent 可以让模型在一个通用循环中决定下一步。AIOps 诊断则有明确阶段和控制要求：先制定计划、一次执行一步、根据证据决定是否继续，并限制最大步数。图让这些控制流显式化、可观察、可测试。

```text
隐式 Agent 循环：控制权更多交给框架和模型
显式 StateGraph：应用定义节点、边、状态和终止条件
```

## 2. State 是唯一共享事实

```python
class PlanExecuteState(TypedDict):
    input: str
    plan: list[str]
    past_steps: Annotated[list[tuple], operator.add]
    response: str
```

- `input`：原始任务，应保持不变。
- `plan`：剩余步骤，会被新值覆盖。
- `past_steps`：已执行 `(step, result)`，通过 `operator.add` 追加。
- `response`：最终回答；非空通常意味着结束。

节点接收当前 State，但只返回要修改的字段：

```python
return {
    "plan": plan[1:],
    "past_steps": [(task, result)],
}
```

LangGraph 根据字段 Reducer 合并更新。没有注解的字段通常使用覆盖策略。

## 3. 构建图

```python
workflow = StateGraph(PlanExecuteState)
workflow.add_node("planner", planner)
workflow.add_node("executor", executor)
workflow.add_node("replanner", replanner)
workflow.set_entry_point("planner")
workflow.add_edge("planner", "executor")
workflow.add_edge("executor", "replanner")
```

Node 可以是普通函数、异步函数或 Runnable。这里三个节点都是异步函数，输入 State，返回状态更新字典。

编译：

```python
compiled_graph = workflow.compile(checkpointer=self.checkpointer)
```

编译会校验图并生成可调用对象。此后可 `ainvoke/astream/get_state`。

## 4. 条件边

Replanner 运行后，`should_continue(state)` 查看合并后的 State：

```text
response 非空 → END
response 为空且 plan 非空 → executor
plan 为空且 response 为空 → END
```

注意代码注释“返回 replanner 生成响应”与实际 `return END` 并不完全一致。不过正常路径中，Replanner 发现 plan 为空会调用 `_generate_response`，所以合并后 response 应非空。异常或空更新时可能直接结束且没有响应，这是测试应覆盖的边界。

## 5. 三个节点的职责

### Planner

输入原始任务，先检索知识，再获取工具清单，将三者交给模型，以 `Plan` 结构化输出返回步骤列表。失败时使用三步默认计划。

### Executor

取剩余计划的第一步，只把当前步骤交给绑定工具的模型。若模型产生 tool calls，则使用 `ToolNode` 执行，再让模型根据工具结果生成该步结论，最后移除当前步骤并追加历史。

### Replanner

结合原始任务、已执行步骤摘要和剩余计划，用结构化 `Act` 决定：

- `continue`：不修改状态，条件边回 Executor。
- `replan`：用 `new_steps` 替换剩余计划。
- `respond`：生成最终回答写入 `response`。

另有 `MAX_STEPS=8` 硬限制，超过后强制回答。

## 6. “返回空字典”不等于停止

Replanner 的 `continue` 返回 `{}`，表示不修改 State。节点执行完成后仍会沿条件边前进。图的控制流由 Edge 决定，不由返回值是否为空决定。

同理，Executor 在 plan 为空时返回 `{}`，但下一个固定边仍会进入 Replanner。

## 7. Checkpoint 和 `thread_id`

`MemorySaver` 在每次图执行时保存状态快照。配置：

```python
config = {"configurable": {"thread_id": session_id}}
```

相同 `thread_id` 表示同一执行线程。Checkpoint 的主要价值：

- 获取最新 State。
- 支持多轮会话。
- 为中断/恢复提供基础。
- 调试每一步状态变化。

但当前 `MemorySaver` 只存内存：进程退出即消失，多 Worker 不共享。它也不会自动从聊天中提取“用户属于支付团队”等长期事实。持久化和长期记忆详见进阶篇。

## 8. 两种流式模式

对话 Agent：

```python
stream_mode="messages"
```

适合逐 Token 文本 UI，通常得到 `(message_chunk, metadata)`。

AIOps：

```python
stream_mode="updates"
```

适合展示工作流进度，事件近似：

```python
{"planner": {"plan": [...]}}
{"executor": {"plan": [...], "past_steps": [...]}}
{"replanner": {"response": "..."}}
```

`AIOpsService` 再把节点更新转换为 `plan`、`step_complete` 等前端事件。

## 9. 初始状态与旧 Checkpoint

`execute()` 每次传入：

```python
{
    "input": user_input,
    "plan": [],
    "past_steps": [],
    "response": ""
}
```

同时又复用 `thread_id`。使用持久 Checkpointer 时，需要明确“新任务”和“恢复旧任务”的语义，避免同一 thread 上意外合并旧状态。常见设计是：`user_id` 表示用户，`thread_id` 表示一次会话或一次诊断任务，另用 `run_id` 标识单次运行。

## 10. 状态图的测试策略

不要只做端到端测试。分别测试：

1. Reducer：两次 `past_steps` 更新是否追加。
2. Planner：给固定模型响应，是否产生期望步骤。
3. Executor：有/无 tool call、工具失败时状态是否正确。
4. Replanner：三种 action 和最大步数分支。
5. Router：给不同 State，下一节点是否正确。
6. Graph：完整运行是否一定终止并生成 response。

通过 Mock 模型和 Tool，可让测试确定且无需真实 API。

## 11. 今日实验

### 实验 A：手算状态

给定初始计划 `[A, B]`，手写 Planner 后、执行 A 后、continue 后、执行 B 后、respond 后的完整 State，再用日志验证。

### 实验 B：最小状态图

实现三个节点：`parse → double → format`，State 包含输入数字、结果和历史。历史字段使用 `operator.add`，用 `updates` 模式打印事件。

### 实验 C：制造异常

让一个 Tool 抛异常，观察 Executor 返回的 `past_steps`、剩余计划和最终报告。验证流程是否继续、报告是否如实说明失败。

### 实验 D：同一 thread 重复执行

使用同一 session 调用两次 AIOps，打印第二次的初始/最终 State，判断是否符合预期。再换新 session 对比。

## 12. 今日自测

1. Node 返回的是完整 State 吗？
2. `past_steps` 为什么追加而 `plan` 会覆盖？
3. Replanner 返回 `{}` 后为什么还会执行？
4. `updates` 和 `messages` 的消费方各是谁？
5. `thread_id`、`user_id` 和一次运行应该如何区分？
6. 为什么持久化 Checkpoint 仍不等于长期记忆？

能根据任意一条 AIOps 日志还原节点和 State 变化，即完成第 4 天。

