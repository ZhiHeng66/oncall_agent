from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from pydantic import ValidationError

from app.auth.contracts import CurrentUser
from app.auth.tokens import InvalidAccessTokenError, create_access_token, decode_access_token
from app.config import Settings


def auth_settings() -> Settings:
    return Settings(
        _env_file=None,
        auth_enabled=True,
        jwt_secret_key="a" * 48,
        checkpointer_backend="memory",
    )


def test_access_token_round_trip_uses_stable_user_identity() -> None:
    settings = auth_settings()
    user = CurrentUser(user_id=uuid4(), username="alice", role="user")

    token = create_access_token(user, settings)
    claims = decode_access_token(token, settings)

    assert claims.sub == user.user_id
    assert claims.token_type == "access"
    assert claims.exp > claims.iat


def test_expired_or_wrong_algorithm_token_is_rejected() -> None:
    settings = auth_settings()
    user = CurrentUser(user_id=uuid4(), username="alice", role="user")
    expired = create_access_token(user, settings, now=datetime.now(UTC) - timedelta(hours=1))
    wrong_algorithm = jwt.encode(
        {
            "sub": str(user.user_id),
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "token_type": "access",
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm="HS384",
    )

    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(expired, settings)
    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(wrong_algorithm, settings)


def test_auth_enabled_requires_a_real_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(_env_file=None, auth_enabled=True, jwt_secret_key="replace-with-a-secret")
