import pytest

from app.cli import users


def test_password_prompt_does_not_write_the_password(capsys, monkeypatch) -> None:
    values = iter(["secret-value", "secret-value"])
    monkeypatch.setattr(users, "getpass", lambda _: next(values))

    assert users._read_password() == "secret-value"
    assert "secret-value" not in capsys.readouterr().out


def test_password_prompt_rejects_mismatched_values(monkeypatch) -> None:
    values = iter(["first", "second"])
    monkeypatch.setattr(users, "getpass", lambda _: next(values))

    with pytest.raises(ValueError, match="confirmation"):
        users._read_password()
