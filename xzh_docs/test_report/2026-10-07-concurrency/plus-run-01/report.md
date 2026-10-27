# 简易阶梯并发测试执行报告

状态：completed_requested_batches

本报告由实际接口请求生成；未执行档位不填性能数据。

## 环境与限制

```json
{
  "utc_started": "2026-10-07T14:12:48.318639+00:00",
  "platform": "macOS-26.5.1-arm64-arm-64bit-Mach-O",
  "python": "3.13.9",
  "httpx": "0.28.1",
  "base_url": "http://127.0.0.1:9901",
  "commit": "c4968257aba423c5b206f67532e0ccf1f8057685",
  "branch": "multi-agents",
  "dirty_worktree": true,
  "model_configuration": "not inspected: server-owned; no .env read",
  "model_quota": "unknown",
  "server_workers": "unknown",
  "milvus_document_count": "unknown",
  "billing": "not measured",
  "stop_policy": "protocol/persistence failure; >=20% failed requests; >=2 rate limits; partial is reported separately",
  "startup_window_s": 3.0,
  "timeout_s": 300.0,
  "batch_pause_s": 30.0,
  "max_requests": 579,
  "max_duration_s": 7200.0,
  "test_username": "loadtest_4d151ad75f60",
  "test_user_id": "e86a2c38-ce88-4940-9d99-75038d4fb595",
  "account_role": "user",
  "account_creation": "local PostgreSQL + project Argon2",
  "account_disabled_after_test": true,
  "token_expires_in_s": 1800,
  "resource_observation": {
    "method": "macOS ps sampled every ~2s; CPU is ps average, not instant utilization",
    "listener_pids": [
      "23095"
    ],
    "samples": 586,
    "peak_rss_kib": 2113504,
    "max_observed_ps_cpu_percent": 99.1
  }
}
```

- 已有未提交修改，因此 commit 不代表完整被测源码快照。
- 本机发压，未做持续固定并发或生产容量认证。
- 业务状态 completed 不等于答案语义正确；内容验收见独立汇总报告。
- 示例工单来自仓库模拟数据，文档样本须由低负载预检验证。
- 首答案指标统计产生答案的请求；完成耗时只统计 completed，失败独立披露。
- P95 采用 nearest-rank，小样本仅作观察。

## 预检与持久化抽查

```json
[
  {
    "check": "local_port",
    "port": 9901,
    "reachable": true
  },
  {
    "check": "local_port",
    "port": 5432,
    "reachable": true
  },
  {
    "check": "local_port",
    "port": 19530,
    "reachable": true
  },
  {
    "check": "local_port",
    "port": 8003,
    "reachable": true
  },
  {
    "check": "local_port",
    "port": 8004,
    "reachable": true
  },
  {
    "check": "local_port",
    "port": 8005,
    "reachable": true
  },
  {
    "check": "/health",
    "http_status": 200
  },
  {
    "check": "/api/auth/me",
    "http_status": 200
  },
  {
    "check": "/api/agent/threads",
    "http_status": 200
  },
  {
    "check": "persistence",
    "request_id": "baseline-knowledge-1-0",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "baseline-knowledge-2-1",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "baseline-knowledge-3-2",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "baseline-data-1-0",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "baseline-data-2-1",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "baseline-data-3-2",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "baseline-complex-1-0",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "baseline-complex-2-1",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "baseline-complex-3-2",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-10-1-0",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-10-1-1",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-10-1-2",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-10-2-0",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-10-2-1",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-10-2-2",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-10-3-0",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-10-3-1",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-10-3-2",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-30-1-0",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-30-1-1",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-30-1-2",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-30-2-0",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-30-2-1",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-30-2-2",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-30-3-0",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-30-3-1",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-30-3-2",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-50-1-0",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-50-1-1",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-50-1-2",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-50-2-0",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-50-2-1",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-50-2-2",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-50-3-0",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-50-3-1",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-50-3-2",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-100-1-0",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-100-1-1",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-100-1-2",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-100-2-0",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-100-2-1",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-100-2-2",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-100-3-0",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-100-3-1",
    "run_ok": true,
    "trace_ok": true
  },
  {
    "check": "persistence",
    "request_id": "mixed-100-3-2",
    "run_ok": true,
    "trace_ok": true
  }
]
```

## 实测批次

|批次|目标并发|实际峰值|请求数|completed|partial|其他|协议通过|完成均值 ms|完成 P95 ms|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
|baseline-knowledge-1|1|1|1|1|0|0|1|11591.22|11591.22|
|baseline-knowledge-2|1|1|1|1|0|0|1|7264.29|7264.29|
|baseline-knowledge-3|1|1|1|1|0|0|1|6550.93|6550.93|
|baseline-data-1|1|1|1|1|0|0|1|4141.93|4141.93|
|baseline-data-2|1|1|1|1|0|0|1|6258.79|6258.79|
|baseline-data-3|1|1|1|1|0|0|1|5336.44|5336.44|
|baseline-complex-1|1|1|1|1|0|0|1|29123.7|29123.7|
|baseline-complex-2|1|1|1|1|0|0|1|17364.64|17364.64|
|baseline-complex-3|1|1|1|1|0|0|1|16899.24|16899.24|
|mixed-10-1|10|10|10|10|0|0|10|10406.07|25593.07|
|mixed-10-2|10|10|10|10|0|0|10|15856.61|79799.74|
|mixed-10-3|10|10|10|10|0|0|10|10142.38|30306.44|
|mixed-30-1|30|30|30|29|1|0|30|11020.11|29989.05|
|mixed-30-2|30|30|30|30|0|0|30|9853.95|29028.0|
|mixed-30-3|30|30|30|28|2|0|30|9423.68|28844.01|
|mixed-50-1|50|50|50|50|0|0|50|13956.16|48478.46|
|mixed-50-2|50|50|50|49|1|0|50|14348.45|30336.11|
|mixed-50-3|50|50|50|46|4|0|50|11519.95|29056.8|
|mixed-100-1|100|100|100|100|0|0|100|26006.3|46988.1|
|mixed-100-2|100|100|100|98|1|1|100|25043.78|41425.28|
|mixed-100-3|100|100|100|96|4|0|100|23016.26|39320.02|

未执行档位：[]

## 结果文件

- `results.json`：逐请求数据、错误分类、实际路由、线程清单与批次汇总。
- `requests.csv`：执行 Agent 请求后生成的逐请求表。
- `samples.json`：本次使用的公开仓库样本。

不保存 Token、原始模型响应、原始异常、完整回答或工具输出。
测试线程未自动删除，便于复核 Run/Trace；只可按本报告线程清单定向清理。
