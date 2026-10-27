"""Summarize completed local attempts, querying only their dedicated test accounts."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path
from uuid import UUID


def query(statement: str) -> dict:
    result = subprocess.run(
        ["docker", "exec", "-i", "oncall-postgres", "sh", "-c",
         'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At'],
        input=statement, capture_output=True, text=True,
    )
    if result.returncode:
        raise RuntimeError("report_database_query_failed")
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--attempts", nargs="+", default=["real-run", "real-run-02"])
    parser.add_argument("--output-name", default="execution-summary.json")
    args = parser.parse_args()
    attempts = []
    content_checks = []
    for name in args.attempts:
        payload = json.loads((args.root / name / "results.json").read_text())
        owner = str(UUID(payload["environment"]["test_user_id"]))
        database = query(
            "SELECT json_build_object('account', "
            f"(SELECT json_build_object('role',role,'is_active',is_active) FROM users WHERE user_id='{owner}'), "
            "'runs', COALESCE((SELECT json_agg(json_build_object('run_id',run_id, "
            "'response',response_json,'metrics',metrics_json)) FROM agent_runs "
            f"WHERE owner_user_id='{owner}'), '[]'::json), "
            "'working_memory_count', (SELECT count(*) FROM agent_working_memories "
            f"WHERE owner_user_id='{owner}'));"
        )
        by_id = {record["run_id"]: record for record in database["runs"]}
        for row in payload["rows"]:
            if row["run_id"] not in by_id:
                content_checks.append({"attempt": name, "request_id": row["request_id"],
                                       "business_status": row["business_status"],
                                       "persisted_run_missing": True})
                continue
            response = by_id[row["run_id"]]["response"]
            metrics = by_id[row["run_id"]]["metrics"]
            answer = response.get("answer", "")
            errors = response.get("errors", [])
            marker_text = " ".join(errors).lower()
            check = {
                "attempt": name, "request_id": row["request_id"],
                "scenario": row["scenario"], "sample_id": row["sample_id"],
                "business_status": row["business_status"],
                "termination_reason": response["termination_reason"],
                "source_types": sorted({s["source_type"] for s in response.get("sources", [])}),
                "has_citation": "[来源" in answer,
                "has_public_error": bool(errors),
                "provider_arrearage": "arrearage" in marker_text,
                "no_authorized_tool_error": any("没有可用于该任务的只读数据工具" in e for e in errors),
                "dispatch_count": metrics["dispatch_count"],
                "tool_call_count": metrics["tool_call_count"],
                "actual_routes": row["actual_routes"],
            }
            if row["scenario"] == "knowledge" and row["sample_id"] == 0:
                check["expected_constraint_present"] = "身份认证" in answer and "验收" in answer
            if row["scenario"] == "knowledge" and row["sample_id"] == 1:
                check["expected_sla_markers_present"] = all(s in answer for s in ("P1", "P2", "P3"))
                check["expected_sla_values_present"] = all(
                    s in answer for s in ("30", "2", "工作日")
                )
            if row["scenario"] == "knowledge" and row["sample_id"] == 2:
                check["expected_faq_markers_present"] = "MCP" in answer and "知识" in answer
            if row["scenario"] == "data":
                check["dispatched_data"] = any(
                    "data" in route.split(":", 1)[-1].split(",")
                    for route in row["actual_routes"]
                )
                ticket_id, state = [("TKT-1001", "open"), ("TKT-1002", "resolved"),
                                    ("TKT-1003", "pending")][row["sample_id"]]
                check["expected_ticket_and_state_present"] = ticket_id in answer and state in answer
                check["other_ticket_id_present"] = any(
                    other in answer for other in ("TKT-1001", "TKT-1002", "TKT-1003")
                    if other != ticket_id
                )
            if row["scenario"] == "complex":
                check["expected_ticket_present"] = (
                    ["TKT-1001", "TKT-1002", "TKT-1003"][row["sample_id"]] in answer
                )
                check["has_knowledge_and_data_sources"] = (
                    "knowledge" in check["source_types"]
                    and any(s != "knowledge" for s in check["source_types"])
                )
            content_checks.append(check)
        attempts.append({
            "attempt": name, "stop_reason": payload["stop_reason"],
            "requests": len(payload["rows"]),
            "statuses": dict(Counter(row["business_status"] for row in payload["rows"])),
            "protocol_pass": sum(row["protocol_ok"] for row in payload["rows"]),
            "account": database["account"],
            "persisted_runs": len(database["runs"]),
            "working_memory_count": database["working_memory_count"],
            "resource_observation": payload["environment"].get("resource_observation"),
            "batches": payload["batches"],
            "persistence_checks": sum(c.get("check") == "persistence"
                                      for c in payload["checks"]),
            "persistence_checks_pass": sum(c.get("check") == "persistence"
                                           and c.get("run_ok") and c.get("trace_ok")
                                           for c in payload["checks"]),
        })
    summary = {"attempts": attempts, "content_checks": content_checks,
               "submitted_requests": sum(a["requests"] for a in attempts),
               "counts": dict(Counter(c["business_status"] for c in content_checks)),
               "stop_reasons": [a["stop_reason"] for a in attempts],
               "levels_not_executed": [n for n in (10, 30, 50, 100)
                                       if not any(b["target"] == n for a in attempts
                                                  for b in a["batches"])],
               "semantic_check_limit": "keyword/evidence checks only, not full answer grading"}
    (args.root / args.output_name).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: summary[key] for key in
                      ("submitted_requests", "counts", "stop_reasons", "levels_not_executed")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
