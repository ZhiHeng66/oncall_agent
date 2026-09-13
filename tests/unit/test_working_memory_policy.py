from datetime import UTC, datetime, timedelta

import pytest

from app.agent.memory.policy import (
    dynamic_evidence_valid_until,
    query_scope_fingerprint,
)
from app.config import Settings


@pytest.mark.parametrize(
    ("tool_name", "server_name", "category", "expected_seconds"),
    [
        ("query_alerts", "operations", "external_data", 10),
        ("query", "monitor", "external_data", 20),
        ("get_ticket", "service-desk", "external_data", 30),
        ("query", "orders", "external_data", 40),
    ],
)
def test_dynamic_evidence_ttl_uses_central_provenance_policy(
    tool_name: str,
    server_name: str,
    category: str,
    expected_seconds: int,
) -> None:
    settings = Settings(
        _env_file=None,
        dashscope_api_key="",
        working_memory_alert_ttl_seconds=10,
        working_memory_metric_ttl_seconds=20,
        working_memory_ticket_ttl_seconds=30,
        working_memory_default_dynamic_ttl_seconds=40,
    )
    now = datetime(2026, 9, 13, tzinfo=UTC)

    valid_until = dynamic_evidence_valid_until(
        now,
        tool_name=tool_name,
        server_name=server_name,
        category=category,
        settings=settings,
    )

    assert valid_until == now + timedelta(seconds=expected_seconds)


def test_query_scope_fingerprint_is_stable_and_does_not_retain_arguments() -> None:
    first = query_scope_fingerprint({"region": "cn-shanghai", "service": "auth"})
    reordered = query_scope_fingerprint({"service": "auth", "region": "cn-shanghai"})
    different = query_scope_fingerprint({"region": "cn-beijing", "service": "auth"})

    assert first == reordered
    assert first != different
    assert "cn-shanghai" not in first
    assert "auth" not in first
