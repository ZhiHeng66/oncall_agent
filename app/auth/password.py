"""Password hashing helpers that keep plain-text secrets out of persistence."""

from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Return an Argon2 hash for a non-empty password."""

    if not password:
        raise ValueError("password cannot be blank")
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its stored hash."""

    if not password or not password_hash:
        return False
    return _password_hash.verify(password, password_hash)
