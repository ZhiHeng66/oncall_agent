"""Run the fixed M5 offline evaluation suite without external services."""

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.agent.contracts import Evidence
from app.agent.specialists.reviewer.agent import ReviewerAgent
from app.agent.supervisor.agent import SupervisorAgent


class FailingRouter:
    async def decide(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("offline fallback")


def _evidence_valid(answer: str, evidence_count: int) -> bool:
    # Keep this contract evaluator independent of the Reviewer implementation.
    citations = [int(item) for item in re.findall(r"\[来源(\d+)]", answer)]
    return bool(evidence_count) and bool(citations) and all(
        1 <= citation <= evidence_count for citation in citations
    )


async def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    kind = case["kind"]
    actual: Any
    if kind == "route":
        decision = await SupervisorAgent(router=FailingRouter()).route(
            case["query"], [], None, 1
        )
        actual = {"mode": decision.mode, "agents": decision.target_agents}
    elif kind == "retrieval":
        actual = _evidence_valid(case["answer"], case["evidence_count"])
    elif kind == "tool_choice":
        allowed = set(case["allowed_tools"])
        actual = [name for name in case["proposed_tools"][: case["max_calls"]] if name in allowed]
    elif kind == "task_completion":
        statuses = set(case["statuses"])
        if statuses == {"failed"}:
            actual = "failed"
        elif "failed" in statuses or "partial" in statuses:
            actual = "partial"
        else:
            actual = "completed"
    elif kind == "reviewer":
        evidence = [
            Evidence(
                source_type="knowledge",
                source_name=f"doc-{index}.md",
                content="fixed evidence",
            )
            for index in range(case["evidence_count"])
        ]
        review = ReviewerAgent._deterministic_review(
            case["answer"], evidence, case["errors"]
        )
        actual = review.retry_target if review else "none"
    else:
        raise ValueError(f"Unknown eval kind: {kind}")
    expected = case["expected"]
    return {
        "id": case["id"],
        "kind": kind,
        "passed": actual == expected,
        "expected": expected,
        "actual": actual,
    }


async def run_dataset(dataset_path: Path, label: str = "current") -> dict[str, Any]:
    raw = dataset_path.read_bytes()
    dataset = json.loads(raw)
    results = [await evaluate_case(case) for case in dataset["cases"]]
    categories: dict[str, dict[str, int | float]] = {}
    for kind in sorted({item["kind"] for item in results}):
        selected = [item for item in results if item["kind"] == kind]
        passed = sum(item["passed"] for item in selected)
        categories[kind] = {
            "passed": passed,
            "total": len(selected),
            "score": passed / len(selected),
        }
    passed = sum(item["passed"] for item in results)
    return {
        "dataset_version": dataset["version"],
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "implementation_label": label,
        "profile": "offline_contract",
        "external_services": False,
        "passed": passed,
        "total": len(results),
        "score": passed / len(results),
        "categories": categories,
        "results": results,
    }


def compare(current: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    summary = {
        "baseline_score": baseline["score"] if baseline else None,
        "current_score": current["score"],
        "score_delta": current["score"] - baseline["score"] if baseline else None,
        "same_dataset": (
            baseline.get("dataset_sha256") == current["dataset_sha256"] if baseline else None
        ),
    }
    return {"comparison": summary, "current": current}


async def async_main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("tests/evals/fixtures/m5_eval_dataset.json"),
    )
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--label", default="current")
    args = parser.parse_args()
    current = await run_dataset(args.dataset, args.label)
    baseline_document = json.loads(args.baseline.read_text()) if args.baseline else None
    baseline = (
        baseline_document.get("current", baseline_document) if baseline_document else None
    )
    report = compare(current, baseline)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    if current["passed"] != current["total"]:
        raise SystemExit(1)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
