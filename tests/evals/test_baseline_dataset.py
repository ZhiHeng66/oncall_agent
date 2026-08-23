import json
from pathlib import Path


def test_baseline_dataset_contains_representative_unscored_requests() -> None:
    dataset_path = Path(__file__).parent / "fixtures" / "baseline_requests.json"
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))

    assert 10 <= len(cases) <= 20
    assert all({"id", "category", "input", "current_entrypoint"} <= case.keys() for case in cases)
    assert all("score" not in case for case in cases)
    assert {case["current_entrypoint"] for case in cases} == {"chat", "aiops"}
