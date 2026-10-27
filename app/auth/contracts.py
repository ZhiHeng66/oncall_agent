"""Typed contracts for local user authentication."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

UserRole = Literal["user", "admin"]


class CurrentUser(BaseModel):
    """Trusted identity available after access-token verification."""

    user_id: UUID
    username: str
    role: UserRole


class UserRecord(CurrentUser):
    """Server-only user record; its password hash must never enter an API response."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    password_hash: str = Field(repr=False)
    is_active: bool
    created_at: datetime
    updated_at: datetime


class NewUser(BaseModel):
    username: str = Field(min_length=3, max_length=128)
    password_hash: str = Field(min_length=1)
    role: UserRole = "user"

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("username cannot be blank")
        return normalized


class AccessTokenClaims(BaseModel):
    sub: UUID
    exp: datetime
    iat: datetime
    token_type: Literal["access"]


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
