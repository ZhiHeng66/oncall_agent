"""Initialize application and LangGraph PostgreSQL schemas."""

import argparse
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agent.runtime.checkpointer import create_postgres_pool
from app.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def upgrade_application_schema(settings: Settings) -> None:
    alembic_config = Config(PROJECT_ROOT / "alembic.ini")
    database_url = settings.database_url.replace("%", "%%")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")


async def upgrade_checkpoint_schema(settings: Settings) -> None:
    pool = create_postgres_pool(settings)
    await pool.open(wait=True)
    try:
        await AsyncPostgresSaver(pool).setup()
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["upgrade"])
    args = parser.parse_args()
    settings = Settings()
    if not settings.database_url.strip():
        parser.error("DATABASE_URL is required")
    if args.command == "upgrade":
        upgrade_application_schema(settings)
        asyncio.run(upgrade_checkpoint_schema(settings))


if __name__ == "__main__":
    main()
