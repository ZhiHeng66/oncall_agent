from app.agent.specialists.data.utils import sanitize_tool_output


def test_sanitize_tool_output_redacts_sensitive_fields_and_inline_authorization() -> None:
    output = {
        "authorization": "Bearer private-token",
        "nested": {"password": "private-password"},
        "message": "Authorization: Bearer another-private-token failed",
    }

    sanitized = sanitize_tool_output(output, max_chars=500)

    assert "private-token" not in sanitized
    assert "private-password" not in sanitized
    assert "another-private-token" not in sanitized
    assert "[REDACTED]" in sanitized
