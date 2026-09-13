"""Server-owned temporal policy for dynamic data evidence."""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from app.config import Settings


def query_scope_fingerprint(arguments: dict[str, Any]) -> str:
    """Return a stable, non-reversible identifier without retaining tool arguments."""

    serialized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def dynamic_evidence_valid_until(
    observed_at: datetime,
    *,
    tool_name: str,
    server_name: str | None,
    category: str,
    settings: Settings,
) -> datetime:
    """Apply centrally configured TTLs according to safe tool provenance."""

    provenance = " ".join((tool_name, server_name or "", category)).lower()
    if "alert" in provenance:
        ttl_seconds = settings.working_memory_alert_ttl_seconds
    elif "ticket" in provenance:
        ttl_seconds = settings.working_memory_ticket_ttl_seconds
    elif "metric" in provenance or "monitor" in provenance:
        ttl_seconds = settings.working_memory_metric_ttl_seconds
    else:
        ttl_seconds = settings.working_memory_default_dynamic_ttl_seconds
    return observed_at + timedelta(seconds=ttl_seconds)
