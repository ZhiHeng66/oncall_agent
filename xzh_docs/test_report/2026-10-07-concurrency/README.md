# 简易阶梯并发测试：真实执行报告

## 最新结果：Plus 重测已执行全部档位

日期：2026-10-07。充值恢复后改用 `qwen-plus` 非思考模式，已完成九条基线及
10/30/50/100 档位各三轮，共 579 条请求：565 completed、13 partial、1 failed。
实际峰值分别达到 10、30、50、100。579 条 SSE 协议检查及 45 条 Run/Trace 抽查通过。

正式最新报告：[plus-test-report.md](plus-test-report.md)；
原始批次：[plus-run-01/report.md](plus-run-01/report.md)；
数据库及内容检查：[plus-summary.json](plus-summary.json)。

不是全部业务验收通过：存在工具失败、复杂任务路由疑点、约 96 秒最长耗时及约 2.02 GiB
RSS 峰值。completed 比例不是回答准确率，本次突发并发结果不是生产容量保证。

Flash 按用户要求中断，历史记录保留在 flash-run-01；旧 stop_reason=running
不表示测试仍在执行。最新测试账号已禁用，9901 测试实例已停止，9900 原服务保持不变。

---

## 以下为充值前两轮历史记录

日期：2026-10-07。历史状态：认证已解决；真实模型服务返回 Arrearage，测试停在单请求基线。

## 结论与实际结果

已通过专用普通用户账号登录，在保留认证的情况下提交了 7 条真实 Agent 请求：
5 条 completed、1 条 partial、1 条 failed。全部请求都收到了唯一终态，ID 一致性检查通过，
7 条 Run/Trace 查询抽查通过，数据库中有对应的 7 条 Run 和 7 份线程工作记忆。

实际最大在途请求数为 1。尚未进入 10、30、50、100 并发，复杂任务场景也尚未执行。
不能据此宣称已完成高并发测试或支持任何较高并发量。

|轮次|请求|状态|首内容片段 ms|终态耗时 ms|
|---|---|---|---:|---:|
|1|知识：交付指南|completed|5690.09|7356.90|
|1|知识：服务等级|completed|6010.09|6944.42|
|1|知识：MCP 故障 FAQ|completed|9911.24|10600.73|
|1|数据：TKT-1001|completed|6614.70|7515.21|
|1|数据：TKT-1002|partial|10133.88|11567.05|
|2|知识：交付指南|completed|5618.95|6402.19|
|2|知识：服务等级|failed|3824.02|3830.16|

最后一条 failed 的内容片段是失败说明，不是正常回答；较短耗时不能作为性能改善。
上述是两次不同尝试的逐请求记录，不将其混算为稳定成功率或容量结论。

## 两轮停止原因

第一轮采用“出现任一非 completed 就暂停”的保守策略。在 TKT-1002 查询中观察到：

- 返回的编号与状态 resolved 匹配仓库示例数据，有 MCP get_ticket 来源。
- Run 记录显示两次 Agent 调度和一次工具调用，termination_reason 为 review_passed。
- errors 仍保留“没有可用于该任务的只读数据工具。”，业务状态因此为 partial。
- 最终答案抽查未出现错误披露，尽管 errors 字段保留了失败；这是值得后续检查的回答质量问题。

这里发生的是单请求业务异常和后续返工，不是并发负载造成的失败。
工具列表为何为空尚未完成根因定位；不能仅根据错误文本判断是模型选错工具还是发现异常。
本次没有修改业务代码去绕过它。

为使 partial 被正确统计而不是直接等同于服务故障，第二轮事先调整停止规则为：
协议/持久化错误立即停止，单批失败比例达到 20% 或出现至少两次模型限流时停止。
partial 独立统计。两轮结果分开保留，不隐藏第一轮失败或将重试计作原请求成功。

第二轮在并发为 1 的知识查询中失败。数据库中的公开 errors 同时显示查询 Embedding
与模型审核受阻，供应商返回 HTTP 400、错误类型/代码 Arrearage，并提示账户状态及欠费。
这不同于应用未登录的 401，也不同于并发配额限流的 429。

在模型单请求已不可用的情况下继续升档，只会测出依赖拒绝访问的速度。因此停止真实
发压，未使用 Fake 模型替代并伪称完成端到端测试。实际欠费金额、配额和调用成本未获取。

## 已完成的实际检查

- 当前工作分支为 `multi-agents`；存在用户已有未提交修改，未改动应用配置和业务源码。
- 本机 API 9900、PostgreSQL 5432、Milvus 19530、MCP 8003/8004/8005 端口可连接。
  端口可连接不等于数据库 Schema、数据集、模型配额已经验收。
- `GET /health` 返回 HTTP 200。
- 早期无凭证请求返回 401，记录保留在 preflight；创建专用账号后 auth/me 和 threads 均返回 200。
- 未读取开发者真实 `.env`。没有修改现有账号或取消认证。
- 每次尝试创建一个随机用户名、普通权限的测试账号，密码只存于执行进程内存，
  使用项目 Argon2 实现生成密码哈希；通过 PostgreSQL 本地容器连接创建。
- 两个测试账号均已禁用，数据库查询确认 role=user、is_active=false。
- 测试线程和 Run 保留供复核，没有删除原有数据；清单位于各轮 results.json。
- 发压机器为 macOS arm64，10 个逻辑 CPU，24 GiB 内存，服务与客户端同机。
- Milvus 检查到 1 个集合 biz、26 条 entity；这是向量实体数，不等于文档数。
- API 是已有运行进程，未重启；实际加载源码与当前工作区的一致性未独立验证。
- 模型配置、模型配额和 Worker 部署数未从真实 `.env` 读取，不能以源码默认值冒充实测配置。

## 基础内容与资源观察

4 条 completed 知识回答均含 knowledge 来源和引用标记，并包含样本所需的关键概念。
两条工单回答包含预期编号与 open/resolved 状态，未出现另一测试工单编号。
这属于关键词与来源抽查，不是完整语义正确性评测，也不证明跨用户隔离。

每约 2 秒使用 macOS ps 采样 API 监听进程 RSS 与 %CPU。第一轮 RSS 峰值
198640 KiB（约 194 MiB），第二轮 179488 KiB（约 175 MiB）。
ps 的 %CPU 是其平均利用率口径，不能解读为瞬时峰值；短时单请求观察也不能证明没有
内存泄漏或测出资源极限。原始样本在各轮 resources.json。

没有采集完整应用日志、生产用户数据或供应商账单。token/成本指标为空，费用未知。

## 脚本及校验

发压脚本：`tools/load_test_agent.py`。

专用账号创建、登录刷新和资源观察：`tools/run_local_load_test.py`。

仅查询本次测试账号的数据库核对与汇总：`tools/report_local_load_test.py`。

新增离线测试：`tests/unit/test_load_test_agent.py`。

执行：

```bash
.venv/bin/python -m pytest -q --no-cov tests/unit/test_load_test_agent.py
```

结果：10 项通过。覆盖多行 SSE、心跳忽略、不完整事件拒绝、三类终态业务状态判定、
重复终态/错误线程检测、HTTP 200 无终态不算成功，以及欠费/限流/工具错误分类。
上述三个脚本和测试文件的 Ruff 检查通过。

这些测试使用 HTTPX MockTransport，只验证发压客户端的记录与判断逻辑，
不能作为平台真实并发性能结果。

执行环境预检：

```bash
.venv/bin/python tools/load_test_agent.py \
  --output xzh_docs/test_report/2026-10-07-concurrency/preflight
```

早期预检结果为 blocked_authentication，当时提交 Agent 请求数为 0。
后续已解决认证并开展两次真实基线尝试，不能只使用这份早期预检描述最终状态。

预检明细：[report.md](preflight/report.md)；机器可读记录：[results.json](preflight/results.json)。

## 结果文件

- [execution-summary.json](execution-summary.json)：两轮状态、内容抽查、账号禁用及数据库核对。
- [第一轮 report.md](real-run/report.md)：5 条请求及严格停止策略下的结果。
- [第二轮 report.md](real-run-02/report.md)：2 条请求，停止在模型账户错误。
- 各轮 requests.csv、results.json：逐请求耗时、实际路由、事件与持久化校验。
- 各轮 samples.json：公开仓库样本，不含敏感业务文档。
- 各轮 resources.json：API 进程的定时资源观察。

第一轮旧版错误分类将工具问题记为 tool_or_mcp，第二轮旧版将 Arrearage 记为
other_disclosed；保留原始测量文件，实际供应商错误通过 execution-summary.json 和本报告补充。
后续脚本已单独识别 model_billing_or_account。

## 恢复执行

先恢复服务端模型与 Embedding 账户的可用状态，再从新账号、新线程和新输出目录重测。
无需提供个人账号或模型 Key，脚本会创建专用账号，并在每批开始前正常登录刷新 Token。

```bash
.venv/bin/python tools/run_local_load_test.py \
  --output xzh_docs/test_report/2026-10-07-concurrency/real-run-03 \
  --levels 10 30 50 100 \
  --repeats 3 \
  --window 3 \
  --pause 30 \
  --timeout 300 \
  --max-requests 579 \
  --max-duration 7200
```

执行包含三类场景各 3 次串行基线，再进入混合并发档位。默认最多提交
9 + 3 × (10 + 30 + 50 + 100) = 579 个 Agent 请求；它们产生的模型调用数更多，
该请求上限不是费用保证。每批独立线程、40% 知识/40% 数据/20% 复杂任务。

后续脚本使用第二轮停止策略。认证失效、预算或时长边界也会停止。
测试线程不会自动删除，输出会保存清单以便复核和定向清理。

结果目录包含 `report.md`、`results.json`、`samples.json`，实际执行请求后增加
`requests.csv`。仅记录 ID、状态、耗时、实际路由、来源类型和错误分类；不记录完整回答、
原始异常或工具输出。

面试可表述为：“已完成真实链路单请求预检，发现一次业务 partial 和模型账户错误；
SSE 终态与 Run/Trace 校验通过，高并发档位待恢复模型服务后继续。”
不可表述为“已验证 100 并发”或“并发失败证明服务吞吐不足”。
