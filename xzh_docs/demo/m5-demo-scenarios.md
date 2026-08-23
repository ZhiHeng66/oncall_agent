# M5 可复现演示用例

## 1. 业务知识问答

先运行 `make init` 上传 `business-docs/`，再调用：

```bash
curl -X POST http://localhost:9900/api/agent/run \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"demo","thread_id":"m5-knowledge","message":"根据退款业务规则说明申请条件"}'
```

## 2. 非运维 MCP 工单查询

`make start-ticket` 启动端口 8005 的只读合成数据服务，再请求：

```bash
curl -X POST http://localhost:9900/api/agent/run_stream \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"demo","thread_id":"m5-ticket","message":"查询当前 KnowledgeHub 的 open 工单"}' \
  --no-buffer
```

## 3. Trace 与指标

从响应取得 `run_id`：

```bash
curl http://localhost:9900/api/agent/runs/<run_id>/trace
curl http://localhost:9900/api/agent/metrics
```

## 4. 重启恢复

同一工作区执行 `make restart`，随后重新查询先前的 `thread_id`：

```bash
curl http://localhost:9900/api/agent/threads/m5-knowledge
```

自动化验收对应 `tests/integration/test_persistence_and_observability.py`，不依赖真实模型、
Milvus 或在线 MCP。

## 5. 固定离线 Eval

```bash
make eval
```

数据集固定在 `tests/evals/fixtures/m5_eval_dataset.json`，生成报告位于
`artifacts/evals/latest.json`。提交的基线和验收快照位于 `xzh_docs/eval_reports/`。
该分数是离线契约用例通过率，不是线上回答准确率、召回率或 Groundedness。

