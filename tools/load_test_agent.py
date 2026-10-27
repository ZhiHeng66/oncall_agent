"""Bounded real-API concurrency checks; never load application settings or .env."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import platform
import socket
import statistics
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

SAMPLES = {
    "knowledge": [
        "根据内部企业版交付指南，身份认证联调和最终用户验收有什么先后约束？请引用来源。",
        "根据内部服务等级文档，P1、P2、P3 的首次响应目标分别是什么？请引用来源。",
        "根据内部FAQ，MCP工单服务不可用时，知识问答是否仍可使用？请引用来源。",
    ],
    "data": [
        "只查询当前示例工单 TKT-1001，返回编号、产品、状态和问题摘要。",
        "只查询当前示例工单 TKT-1002，返回编号、产品、状态和问题摘要。",
        "只查询当前示例工单 TKT-1003，返回编号、产品、状态和问题摘要。",
    ],
    "complex": [
        "先查询当前示例工单 TKT-1001 的状态及问题，再检索内部企业版交付指南，"
        "根据查询结果判断能否进行最终用户验收，给出有依据的建议。只查询分析，不执行修改。",
        "先查询当前示例工单 TKT-1002 的状态，再查内部服务等级与升级规则，"
        "分析首次响应目标与解决时间的区别，给出有来源的说明。只查询分析，不执行修改。",
        "先查询当前示例工单 TKT-1003 的状态和问题，再查询内部企业版交付指南，"
        "根据已有证据列出需要确认的交付事项。只查询分析，不执行修改。",
    ],
}


async def sse_events(response: httpx.Response):
    """Parse SSE records, including multiline data and comment heartbeats."""
    data: list[str] = []
    async for line in response.aiter_lines():
        if not line:
            if data:
                yield json.loads("\n".join(data))
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        raise ValueError("incomplete_sse_record")


def category(errors: list[str]) -> str:
    """Classify disclosed errors without persisting raw exceptions or credentials."""
    value = " ".join(errors).lower()
    for label, markers in (
        ("model_billing_or_account", ("arrearage", "overdue-payment", "欠费")),
        ("model_rate_limit", ("429", "rate limit", "ratelimit", "限流", "throttl")),
        ("database", ("pooltimeout", "postgres", "数据库", "connection pool")),
        ("tool_or_mcp", ("mcp", "工具", "tool")),
        ("timeout", ("timeout", "超时")),
        ("model_authentication", ("api key", "api_key", "invalidapikey")),
        ("knowledge", ("知识", "检索", "milvus", "embedding")),
    ):
        if any(marker in value for marker in markers):
            return label
    return "undisclosed" if not value else "other_disclosed"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    durations = sorted(
        row["terminal_ms"] for row in rows
        if row["business_status"] == "completed" and row["terminal_ms"] is not None
    )
    content = [row["first_content_ms"] for row in rows if row["first_content_ms"] is not None]
    return {
        "requests": len(rows),
        "statuses": dict(Counter(row["business_status"] for row in rows)),
        "error_categories": dict(Counter(row["error_category"] for row in rows)),
        "http_200": sum(row["http_status"] == 200 for row in rows),
        "protocol_pass": sum(row["protocol_ok"] for row in rows),
        "completion_rate": sum(row["business_status"] == "completed" for row in rows)
        / max(len(rows), 1),
        "completed_duration_samples": len(durations),
        "completed_mean_ms": round(statistics.mean(durations), 2) if durations else None,
        "completed_p95_ms": durations[math.ceil(len(durations) * .95) - 1] if durations else None,
        "first_content_mean_ms": round(statistics.mean(content), 2) if content else None,
        "actual_routes": dict(Counter(
            "/".join(row["actual_routes"]) or "none" for row in rows
        )),
    }


class LoadTest:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.rows: list[dict[str, Any]] = []
        self.batches: list[dict[str, Any]] = []
        self.threads: list[str] = []
        self.checks: list[dict[str, Any]] = []
        self.run_ids: set[str] = set()
        self.event_ids: set[str] = set()
        self.active = 0
        self.peak = 0
        self.stop_reason = "not_started"
        self.started = time.perf_counter()
        self.metadata: dict[str, Any] = {
            "utc_started": datetime.now(UTC).isoformat(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "httpx": httpx.__version__,
            "base_url": args.base_url,
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "branch": subprocess.check_output(
                ["git", "branch", "--show-current"], text=True
            ).strip(),
            "dirty_worktree": bool(subprocess.check_output(["git", "status", "--short"])),
            "model_configuration": "not inspected: server-owned; no .env read",
            "model_quota": "unknown",
            "server_workers": "unknown",
            "milvus_document_count": "unknown",
            "billing": "not measured",
            "stop_policy": "protocol/persistence failure; >=20% failed requests; >=2 rate limits; partial is reported separately",
            "startup_window_s": args.window,
            "timeout_s": args.timeout,
            "batch_pause_s": args.pause,
            "max_requests": args.max_requests,
            "max_duration_s": args.max_duration,
        }

    def save(self) -> None:
        output = self.args.output
        output.mkdir(parents=True, exist_ok=True)
        payload = {"environment": self.metadata, "stop_reason": self.stop_reason,
                   "batches": self.batches, "checks": self.checks,
                   "created_threads": self.threads, "rows": self.rows}
        (output / "results.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output / "samples.json").write_text(
            json.dumps(SAMPLES, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if self.rows:
            with (output / "requests.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(self.rows[0]))
                writer.writeheader()
                writer.writerows(self.rows)
        lines = ["# 简易阶梯并发测试执行报告", "", "状态：" + self.stop_reason, "",
                 "本报告由实际接口请求生成；未执行档位不填性能数据。", "",
                 "## 环境与限制", "",
                 "```json", json.dumps(self.metadata, ensure_ascii=False, indent=2), "```", "",
                 "- 已有未提交修改，因此 commit 不代表完整被测源码快照。",
                 "- 本机发压，未做持续固定并发或生产容量认证。",
            "- 业务状态 completed 不等于答案语义正确；内容验收见独立汇总报告。",
                 "- 示例工单来自仓库模拟数据，文档样本须由低负载预检验证。",
                 "- 首答案指标统计产生答案的请求；完成耗时只统计 completed，失败独立披露。",
                 "- P95 采用 nearest-rank，小样本仅作观察。", "",
                 "## 预检与持久化抽查", "", "```json",
                 json.dumps(self.checks, ensure_ascii=False, indent=2), "```", "",
                 "## 实测批次", "",
                 "|批次|目标并发|实际峰值|请求数|completed|partial|其他|协议通过|完成均值 ms|完成 P95 ms|",
                 "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for batch in self.batches:
            summary = batch["summary"]
            statuses = summary["statuses"]
            completed, partial = statuses.get("completed", 0), statuses.get("partial", 0)
            lines.append(
                f"|{batch['batch_id']}|{batch['target']}|{batch['peak']}|"
                f"{summary['requests']}|{completed}|{partial}|"
                f"{summary['requests'] - completed - partial}|{summary['protocol_pass']}|"
                f"{summary['completed_mean_ms']}|{summary['completed_p95_ms']}|"
            )
        if not self.batches:
            lines.append("\n没有执行 Agent 请求；不能得出并发能力结论。")
        covered = {batch["target"] for batch in self.batches}
        lines += ["", "未执行档位：" + str([x for x in (1, 10, 30, 50, 100) if x not in covered]),
                  "", "## 结果文件", "",
                  "- `results.json`：逐请求数据、错误分类、实际路由、线程清单与批次汇总。",
                  "- `requests.csv`：执行 Agent 请求后生成的逐请求表。",
                  "- `samples.json`：本次使用的公开仓库样本。", "",
                  "不保存 Token、原始模型响应、原始异常、完整回答或工具输出。",
                  "测试线程未自动删除，便于复核 Run/Trace；只可按本报告线程清单定向清理。"]
        (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    async def request(self, client: httpx.AsyncClient, batch_id: str, target: int,
                      scenario: str, index: int, thread: str, delay: float) -> dict[str, Any]:
        await asyncio.sleep(delay)
        row: dict[str, Any] = {
            "batch_id": batch_id, "scenario": scenario, "sample_id": index % 3,
            "target_concurrency": target, "request_id": f"{batch_id}-{index}",
            "thread_id": thread, "run_id": None, "actual_routes": [],
            "http_status": None, "business_status": "unknown", "error_category": "none",
            "first_event_ms": None, "first_content_ms": None, "terminal_ms": None,
            "elapsed_ms": None, "terminal_count": 0, "event_count": 0,
            "protocol_ok": True, "answer_nonempty": False, "source_types": [],
        }
        started = time.perf_counter()
        self.active += 1
        self.peak = max(self.peak, self.active)
        terminal_result: dict[str, Any] = {}
        try:
            async with asyncio.timeout(self.args.timeout):
                async with client.stream(
                    "POST", "/api/agent/run_stream",
                    json={"thread_id": thread, "message": SAMPLES[scenario][index % 3]},
                ) as response:
                    row["http_status"] = response.status_code
                    if response.status_code != 200:
                        row.update(business_status="http_error", error_category="http_error",
                                   protocol_ok=False)
                    else:
                        async for event in sse_events(response):
                            elapsed = round((time.perf_counter() - started) * 1000, 2)
                            row["event_count"] += 1
                            if row["terminal_count"]:
                                row["protocol_ok"] = False
                            if row["run_id"] is None:
                                row["run_id"] = event.get("run_id")
                                if not row["run_id"] or row["run_id"] in self.run_ids:
                                    row["protocol_ok"] = False
                                self.run_ids.add(row["run_id"])
                            if (event.get("run_id") != row["run_id"]
                                    or event.get("thread_id") != thread):
                                row["protocol_ok"] = False
                            event_id = event.get("event_id")
                            if not event_id or event_id in self.event_ids:
                                row["protocol_ok"] = False
                            self.event_ids.add(event_id)
                            kind, data = event.get("type"), event.get("data", {})
                            if kind == "run_started" and row["first_event_ms"] is None:
                                row["first_event_ms"] = elapsed
                            if kind == "route_decided":
                                row["actual_routes"].append(
                                    str(data.get("mode")) + ":" + ",".join(data.get("target_agents", []))
                                )
                            if (kind == "content_delta" and data.get("content")
                                    and row["first_content_ms"] is None):
                                row["first_content_ms"] = elapsed
                            if kind in {"run_completed", "run_failed"}:
                                row["terminal_count"] += 1
                                row["terminal_ms"] = elapsed
                                terminal_result = data.get("result", {})
                                row["business_status"] = terminal_result.get("status", "failed")
                                if terminal_result:
                                    if (terminal_result.get("run_id") != row["run_id"]
                                            or terminal_result.get("thread_id") != thread):
                                        row["protocol_ok"] = False
                                elif kind == "run_completed":
                                    row["protocol_ok"] = False
                                row["answer_nonempty"] = bool(terminal_result.get("answer", "").strip())
                                row["source_types"] = sorted({
                                    source.get("source_type", "unknown")
                                    for source in terminal_result.get("sources", [])
                                })
                                errors = terminal_result.get("errors", []) or [str(data.get("error", ""))]
                                if row["business_status"] != "completed":
                                    row["error_category"] = category(errors)
        except (TimeoutError, httpx.TimeoutException) as exc:
            row.update(business_status="client_timeout", error_category=type(exc).__name__,
                       protocol_ok=False)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            row.update(business_status="client_or_protocol_error", error_category=type(exc).__name__,
                       protocol_ok=False)
        finally:
            self.active -= 1
            row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        if row["terminal_count"] != 1 or row["first_event_ms"] is None:
            row["protocol_ok"] = False
        if row["business_status"] == "completed" and not row["answer_nonempty"]:
            row["protocol_ok"] = False
        if not row["terminal_count"] and row["business_status"] == "unknown":
            row.update(business_status="missing_terminal", error_category="missing_terminal")
        self.rows.append(row)
        print(json.dumps({k: row[k] for k in (
            "request_id", "business_status", "error_category", "elapsed_ms", "actual_routes"
        )}, ensure_ascii=False), flush=True)
        return row

    async def batch(self, client: httpx.AsyncClient, batch_id: str, scenarios: list[str],
                    sample_offset: int = 0) -> bool:
        threads: list[str] = []
        for _ in scenarios:
            response = await client.post("/api/agent/threads")
            if response.status_code != 200:
                self.stop_reason = f"thread_creation_http_{response.status_code}"
                self.save()
                return False
            thread = response.json()["thread_id"]
            self.threads.append(thread)
            threads.append(thread)
        self.save()
        self.peak = 0
        target = len(scenarios)
        rows = await asyncio.gather(*[
            self.request(client, batch_id, target, scenario, i + sample_offset, thread,
                         self.args.window * i / max(target - 1, 1))
            for i, (scenario, thread) in enumerate(zip(scenarios, threads, strict=True))
        ])
        self.batches.append({"batch_id": batch_id, "target": target,
                             "peak": self.peak, "summary": summarize(rows)})
        # Verify up to three results outside the timed load window; no answer bodies persisted.
        for row in [item for item in rows if item["run_id"] and item["terminal_count"] == 1][:3]:
            check: dict[str, Any] = {"check": "persistence", "request_id": row["request_id"]}
            for endpoint in ("", "/trace"):
                response = await client.get(f"/api/agent/runs/{row['run_id']}{endpoint}")
                body = response.json() if response.status_code == 200 else {}
                result = body.get("run", {}) if endpoint else body
                check["trace_ok" if endpoint else "run_ok"] = (
                    response.status_code == 200 and result.get("run_id") == row["run_id"]
                    and result.get("thread_id") == row["thread_id"]
                    and result.get("status") == row["business_status"]
                )
            self.checks.append(check)
        self.save()
        good = all(row["protocol_ok"] for row in rows)
        if not good:
            self.stop_reason = "stopped_after_protocol_failure"
        failed = sum(row["business_status"] not in {"completed", "partial"} for row in rows)
        rate_limits = sum(row["error_category"] == "model_rate_limit" for row in rows)
        if failed / len(rows) >= .2 or rate_limits >= 2:
            self.stop_reason = "stopped_after_failure_or_rate_limit_threshold"
            good = False
        if any(not check.get("run_ok", True) or not check.get("trace_ok", True)
               for check in self.checks):
            self.stop_reason = "stopped_after_persistence_failure"
            good = False
        self.save()
        return good

    async def execute(self) -> None:
        token = os.environ.get("ONCALL_LOAD_TEST_TOKEN", "")
        if self.args.token_file:
            token = self.args.token_file.read_text(encoding="utf-8").strip()
        token = token.removeprefix("Bearer ").strip()
        headers = {"Authorization": "Bearer " + token} if token else {}
        async with httpx.AsyncClient(
            base_url=self.args.base_url, headers=headers, trust_env=False,
            timeout=httpx.Timeout(connect=10, read=120, write=30, pool=10),
            limits=httpx.Limits(max_connections=120, max_keepalive_connections=120),
        ) as client:
            for port in (urlparse(self.args.base_url).port or 80, 5432, 19530, 8003, 8004, 8005):
                with socket.socket() as sock:
                    sock.settimeout(2)
                    self.checks.append({"check": "local_port", "port": port,
                                        "reachable": sock.connect_ex(("127.0.0.1", port)) == 0})
            for endpoint in ("/health", "/api/auth/me", "/api/agent/threads"):
                response = await client.get(endpoint)
                self.checks.append({"check": endpoint, "http_status": response.status_code})
                if endpoint == "/health" and response.status_code != 200:
                    self.stop_reason = "blocked_health_check"
                    self.save()
                    return
                if endpoint != "/health" and response.status_code != 200:
                    self.stop_reason = "blocked_authentication"
            if self.stop_reason == "blocked_authentication":
                self.save()
                print("Authentication required; no Agent workload submitted.", flush=True)
                return
            self.stop_reason = "running"
            for scenario in SAMPLES:
                for index in range(3):
                    if not await self.batch(client, f"baseline-{scenario}-{index + 1}",
                                            [scenario], sample_offset=index):
                        return
            for target in self.args.levels:
                for repetition in range(self.args.repeats):
                    if (len(self.rows) + target > self.args.max_requests or
                            time.perf_counter() - self.started > self.args.max_duration):
                        self.stop_reason = "stopped_budget_boundary"
                        self.save()
                        return
                    await asyncio.sleep(self.args.pause)
                    scenarios = (["knowledge", "data", "knowledge", "data", "complex"]
                                 * (target // 5))
                    if not await self.batch(client, f"mixed-{target}-{repetition + 1}", scenarios):
                        return
            self.stop_reason = "completed_requested_batches"
            self.save()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:9900")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--levels", type=int, nargs="+", default=[10, 30, 50, 100])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--window", type=float, default=3)
    parser.add_argument("--pause", type=float, default=30)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--max-requests", type=int, default=579)
    parser.add_argument("--max-duration", type=float, default=7200)
    args = parser.parse_args()
    if (any(level not in (10, 30, 50, 100) for level in args.levels)
            or not 1 <= args.repeats <= 3 or args.window < 0 or args.pause < 0
            or args.timeout <= 0 or args.max_requests < 9 or args.max_duration <= 0):
        parser.error("Invalid load/budget parameters")
    test = LoadTest(args)
    try:
        asyncio.run(test.execute())
    except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        test.stop_reason = "execution_error_" + type(exc).__name__
        test.save()
        print(test.stop_reason, flush=True)
    finally:
        test.save()


if __name__ == "__main__":
    main()
