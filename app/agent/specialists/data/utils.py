"""Sanitization helpers for untrusted tool output."""

import json
from typing import Any

from app.agent.mcp_client import sanitize_error_message

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
}


def sanitize_tool_output(value: Any, max_chars: int) -> str:
    """Redact sensitive fields and return a bounded, serializable representation."""

    cleaned = redact_sensitive_data(value)
    if isinstance(cleaned, str):
        serialized = cleaned
    else:
        serialized = json.dumps(cleaned, ensure_ascii=False, default=str)
    return sanitize_error_message(serialized, max_length=max_chars)


def redact_sensitive_data(value: Any) -> Any:
    """Recursively redact credential-shaped fields from public structured data."""

    def scrub(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                str(key): "[REDACTED]" if str(key).lower() in SENSITIVE_KEYS else scrub(child)
                for key, child in item.items()
            }
        if isinstance(item, (list, tuple)):
            return [scrub(child) for child in item]
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
        content = getattr(item, "content", None)
        return scrub(content) if content is not None else str(item)

    return scrub(value)
