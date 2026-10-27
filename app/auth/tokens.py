"""Access-token creation and validation with an explicitly configured algorithm."""

from datetime import UTC, datetime, timedelta

import jwt
from jwt import InvalidTokenError
from pydantic import ValidationError

from app.auth.contracts import AccessTokenClaims, CurrentUser
from app.config import Settings


class InvalidAccessTokenError(ValueError):
    """Raised when an access token cannot establish a trusted identity."""


def create_access_token(
    user: CurrentUser,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> str:
    """Create a short-lived token containing only the stable user identifier."""

    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload = {
        "sub": str(user.user_id),
        "iat": issued_at,
        "exp": expires_at,
        "token_type": "access",
    }
    return jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str, settings: Settings) -> AccessTokenClaims:
    """Validate signature, fixed algorithm and required access-token claims."""

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "iat", "exp", "token_type"]},
        )
        return AccessTokenClaims.model_validate(payload)
    except (InvalidTokenError, ValidationError, ValueError) as exc:
        raise InvalidAccessTokenError("invalid access token") from exc
