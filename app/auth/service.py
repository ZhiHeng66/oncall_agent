"""Lifecycle-managed local authentication service."""

from uuid import UUID

from app.agent.runtime.checkpointer import PostgresPool, create_postgres_pool
from app.auth.contracts import CurrentUser, NewUser, TokenResponse, UserRole
from app.auth.password import hash_password, verify_password
from app.auth.tokens import InvalidAccessTokenError, create_access_token, decode_access_token
from app.config import Settings, config
from app.services.user_store import PostgresUserStore, UserStore


class AuthenticationDisabledError(RuntimeError):
    """Raised when a protected authentication operation is unavailable by configuration."""


class InvalidCredentialsError(ValueError):
    """Raised without revealing whether a username or password was incorrect."""


class AuthenticationService:
    """Authenticate local users without leaking persistence details to API handlers."""

    def __init__(self, settings: Settings = config, users: UserStore | None = None) -> None:
        self._settings = settings
        self._users = users
        self._pool: PostgresPool | None = None

    async def startup(self) -> None:
        if not self._settings.auth_enabled:
            return
        if self._users is None:
            if self._settings.checkpointer_backend != "postgres":
                raise RuntimeError(
                    "Local authentication requires the PostgreSQL persistence backend"
                )
            self._pool = create_postgres_pool(self._settings)
            await self._pool.open(wait=True)
            self._users = PostgresUserStore(self._pool)
        try:
            await self._users.setup()
        except Exception:
            await self.shutdown()
            raise

    async def shutdown(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def create_user(
        self,
        username: str,
        password: str,
        *,
        role: UserRole = "user",
    ) -> CurrentUser:
        user = await self._require_users().create(
            NewUser(username=username, password_hash=hash_password(password), role=role)
        )
        return CurrentUser(user_id=user.user_id, username=user.username, role=user.role)

    async def set_active(self, user_id: UUID, is_active: bool) -> None:
        await self._require_users().set_active(user_id, is_active)

    async def authenticate(self, username: str, password: str) -> TokenResponse:
        user = await self._require_users().get_by_username(username)
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("invalid username or password")
        current_user = CurrentUser(user_id=user.user_id, username=user.username, role=user.role)
        return TokenResponse(
            access_token=create_access_token(current_user, self._settings),
            expires_in=self._settings.jwt_access_token_expire_minutes * 60,
        )

    async def current_user_from_token(self, token: str) -> CurrentUser:
        claims = decode_access_token(token, self._settings)
        user = await self._require_users().get_by_id(claims.sub)
        if user is None or not user.is_active:
            raise InvalidAccessTokenError("invalid access token")
        return CurrentUser(user_id=user.user_id, username=user.username, role=user.role)

    def _require_users(self) -> UserStore:
        if not self._settings.auth_enabled:
            raise AuthenticationDisabledError("local authentication is disabled")
        if self._users is None:
            raise AuthenticationDisabledError("authentication service is not initialized")
        return self._users


authentication_service = AuthenticationService()
