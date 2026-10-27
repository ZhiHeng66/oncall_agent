# 简易阶梯并发测试执行报告

状态：stopped_after_failure_or_rate_limit_threshold

本报告由实际接口请求生成；未执行档位不填性能数据。

## 环境与限制

```json
{
  "utc_started": "2026-10-07T13:46:43.997830+00:00",
  "platform": "macOS-26.5.1-arm64-arm-64bit-Mach-O",
  "python": "3.13.9",
  "httpx": "0.28.1",
  "base_url": "http://127.0.0.1:9900",
  "commit": "c4968257aba423c5b206f67532e0ccf1f8057685",
  "branch": "multi-agents",
  "dirty_worktree": true,
  "model_configuration": "not inspected: server-owned; no .env read",
  "model_quota": "unknown",
  "server_workers": "unknown",
  "milvus_document_count": "unknown",
  "billing": "not measured",
  "stop_policy": "protocol/persistence failure; >=20% failed requests; >=2 rate limits; partial is reported separately",
  "startup_window_s": 3,
  "timeout_s": 300,
  "batch_pause_s": 30,
  "max_requests": 579,
  "max_duration_s": 7200,
  "test_username": "loadtest_bbe482a06b26",
  "test_user_id": "ce3d0706-110d-401d-901b-3f8827604902",
  "account_role": "user",
  "account_creation": "local PostgreSQL + project Argon2",
  "account_disabled_after_test": true,
  "token_expires_in_s": 1800,
  "resource_observation": {
    "method": "macOS ps sampled every ~2s; CPU is ps average, not instant utilization",
    "listener_pids": [
      "63102"
    ],
    "samples": 6,
    "peak_rss_kib": 179488,
    "max_observed_ps_cpu_percent": 4.4
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
    "port": 9900,
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
  }
]
```

## 实测批次

|批次|目标并发|实际峰值|请求数|completed|partial|其他|协议通过|完成均值 ms|完成 P95 ms|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
|baseline-knowledge-1|1|1|1|1|0|0|1|6402.19|6402.19|
|baseline-knowledge-2|1|1|1|0|0|1|1|None|None|

未执行档位：[10, 30, 50, 100]

## 结果文件

- `results.json`：逐请求数据、错误分类、实际路由、线程清单与批次汇总。
- `requests.csv`：执行 Agent 请求后生成的逐请求表。
- `samples.json`：本次使用的公开仓库样本。

不保存 Token、原始模型响应、原始异常、完整回答或工具输出。
测试线程未自动删除，便于复核 Run/Trace；只可按本报告线程清单定向清理。
