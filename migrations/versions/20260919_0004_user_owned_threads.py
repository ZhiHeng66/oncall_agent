"""Add user-owned threads and isolate historical records.

Revision ID: 20260919_0004
Revises: 20260919_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260919_0004"
down_revision: str | None = "20260919_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_USER_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO users (user_id, username, password_hash, role, is_active) "
            "VALUES (CAST(:user_id AS uuid), 'legacy-system', '!', 'user', FALSE) "
            "ON CONFLICT (username) DO NOTHING"
        ).bindparams(user_id=LEGACY_USER_ID)
    )
    op.create_table(
        "agent_threads",
        sa.Column("thread_id", sa.Text(), primary_key=True),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.add_column("agent_runs", sa.Column("owner_user_id", sa.Uuid(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE agent_runs SET owner_user_id = CAST(:user_id AS uuid) "
            "WHERE owner_user_id IS NULL"
        ).bindparams(user_id=LEGACY_USER_ID)
    )
    op.alter_column("agent_runs", "owner_user_id", nullable=False)
    op.create_foreign_key(
        "fk_agent_runs_owner", "agent_runs", "users", ["owner_user_id"], ["user_id"]
    )
    op.execute(
        sa.text(
            "INSERT INTO agent_threads (thread_id, owner_user_id) "
            "SELECT DISTINCT thread_id, CAST(:user_id AS uuid) FROM agent_runs "
            "ON CONFLICT (thread_id) DO NOTHING"
        ).bindparams(user_id=LEGACY_USER_ID)
    )
    # Working-memory rows predate trusted users. Isolate them under the disabled
    # legacy account before converting the original text owner field to UUID.
    op.execute(
        sa.text("UPDATE agent_working_memories SET owner_user_id = CAST(:user_id AS text)").bindparams(
            user_id=LEGACY_USER_ID
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO agent_threads (thread_id, owner_user_id) "
            "SELECT DISTINCT thread_id, CAST(:user_id AS uuid) FROM agent_working_memories "
            "ON CONFLICT (thread_id) DO NOTHING"
        ).bindparams(user_id=LEGACY_USER_ID)
    )
    op.alter_column(
        "agent_working_memories",
        "owner_user_id",
        existing_type=sa.Text(),
        type_=sa.Uuid(),
        postgresql_using="owner_user_id::uuid",
    )
    op.create_foreign_key(
        "fk_agent_working_memories_owner",
        "agent_working_memories",
        "users",
        ["owner_user_id"],
        ["user_id"],
    )
    op.create_foreign_key(
        "fk_agent_working_memories_thread",
        "agent_working_memories",
        "agent_threads",
        ["thread_id"],
        ["thread_id"],
    )
    op.create_foreign_key(
        "fk_agent_runs_thread",
        "agent_runs",
        "agent_threads",
        ["thread_id"],
        ["thread_id"],
    )
    op.create_index(
        "idx_agent_threads_owner_updated",
        "agent_threads",
        ["owner_user_id", "updated_at", "thread_id"],
    )
    op.create_index(
        "idx_agent_runs_owner_thread_created",
        "agent_runs",
        ["owner_user_id", "thread_id", "created_at", "run_id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_agent_runs_thread", "agent_runs", type_="foreignkey")
    op.drop_constraint(
        "fk_agent_working_memories_thread",
        "agent_working_memories",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_agent_working_memories_owner",
        "agent_working_memories",
        type_="foreignkey",
    )
    op.alter_column(
        "agent_working_memories",
        "owner_user_id",
        existing_type=sa.Uuid(),
        type_=sa.Text(),
        postgresql_using="owner_user_id::text",
    )
    op.drop_index("idx_agent_runs_owner_thread_created", table_name="agent_runs")
    op.drop_index("idx_agent_threads_owner_updated", table_name="agent_threads")
    op.drop_constraint("fk_agent_runs_owner", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "owner_user_id")
    op.drop_table("agent_threads")
