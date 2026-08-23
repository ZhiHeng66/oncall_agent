from pathlib import Path

from app.evaluation.runner import run_dataset


async def test_fixed_m5_dataset_passes_all_contract_cases() -> None:
    report = await run_dataset(Path("tests/evals/fixtures/m5_eval_dataset.json"))

    assert report["total"] == 34
    assert report["passed"] == report["total"]
    assert set(report["categories"]) == {
        "retrieval",
        "reviewer",
        "route",
        "task_completion",
        "tool_choice",
    }
    assert report["external_services"] is False
