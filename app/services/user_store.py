"""User persistence for the local single-tenant authentication service."""

import asyncio
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation

from app.agent.runtime.checkpointer import PostgresPool
from app.auth.contracts import NewUser, UserRecord

_GET_USER_BY_ID_SQL = (
    "SELECT user_id, username, password_hash, role, is_active, created_at, updated_at "
    "FROM users WHERE user_id = %s"
)
_GET_USER_BY_USERNAME_SQL = (
    "SELECT user_id, username, password_hash, role, is_active, created_at, updated_at "
    "FROM users WHERE username = %s"
)


class UserAlreadyExistsError(ValueError):
    """Raised when a normalized username is already registered."""


class UserStore(Protocol):
    async def setup(self) -> None: ...

    async def get_by_id(self, user_id: UUID) -> UserRecord | None: ...

    async def get_by_username(self, username: str) -> UserRecord | None: ...

    async def create(self, user: NewUser) -> UserRecord: ...

    async def set_active(self, user_id: UUID, is_active: bool) -> None: ...


class InMemoryUserStore:
    """Test-only user store with the same behavior as the PostgreSQL implementation."""

    def __init__(self) -> None:
        self._users: dict[UUID, UserRecord] = {}
        self._usernames: dict[str, UUID] = {}
        self._lock = asyncio.Lock()

    async def setup(self) -> None:
        return None

    async def get_by_id(self, user_id: UUID) -> UserRecord | None:
        async with self._lock:
            return self._users.get(user_id)

    async def get_by_username(self, username: str) -> UserRecord | None:
        async with self._lock:
            user_id = self._usernames.get(username.strip().casefold())
            return self._users.get(user_id) if user_id else None

    async def create(self, user: NewUser) -> UserRecord:
        async with self._lock:
            if user.username in self._usernames:
                raise UserAlreadyExistsError("username already exists")
            now = datetime.now(UTC)
            record = UserRecord(
                user_id=uuid4(),
                username=user.username,
                password_hash=user.password_hash,
                role=user.role,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            self._users[record.user_id] = record
            self._usernames[record.username] = record.user_id
            return record

    async def set_active(self, user_id: UUID, is_active: bool) -> None:
        async with self._lock:
            record = self._users.get(user_id)
            if record is None:
                return
            self._users[user_id] = record.model_copy(
                update={"is_active": is_active, "updated_at": datetime.now(UTC)}
            )


class PostgresUserStore:
    """PostgreSQL user directory using the application lifespan connection pool."""

    def __init__(self, pool: PostgresPool) -> None:
        self._pool = pool

    async def setup(self) -> None:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT to_regclass('users') AS users")
                row = await cursor.fetchone()
        if row is None or row["users"] is None:
            raise RuntimeError("User schema is not initialized; run the database upgrade command")

    async def get_by_id(self, user_id: UUID) -> UserRecord | None:
        return await self._fetch_one(_GET_USER_BY_ID_SQL, (user_id,))

    async def get_by_username(self, username: str) -> UserRecord | None:
        return await self._fetch_one(_GET_USER_BY_USERNAME_SQL, (username.strip().casefold(),))

    async def create(self, user: NewUser) -> UserRecord:
        try:
            async with self._pool.connection() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """INSERT INTO users (user_id, username, password_hash, role, is_active)
                        VALUES (%s, %s, %s, %s, TRUE)
                        RETURNING user_id, username, password_hash, role, is_active,
                                  created_at, updated_at""",
                        (uuid4(), user.username, user.password_hash, user.role),
                    )
                    row = await cursor.fetchone()
        except UniqueViolation as exc:
            raise UserAlreadyExistsError("username already exists") from exc
        if row is None:
            raise RuntimeError("User creation did not return a record")
        return UserRecord.model_validate(row)

    async def set_active(self, user_id: UUID, is_active: bool) -> None:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """UPDATE users SET is_active = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = %s""",
                    (is_active, user_id),
                )

    async def _fetch_one(self, query: str, parameters: tuple[object, ...]) -> UserRecord | None:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(query, parameters)
                row = await cursor.fetchone()
        return UserRecord.model_validate(row) if row else None
