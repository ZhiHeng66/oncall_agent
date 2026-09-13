"""Create public Agent run and event tables.

Revision ID: 20260828_0001
Revises:
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("request_message", sa.Text(), nullable=False),
        sa.Column("response_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(
        "idx_agent_runs_thread",
        "agent_runs",
        ["thread_id", "created_at", "run_id"],
        unique=False,
    )
    op.create_table(
        "agent_events",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("envelope_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("run_id", "sequence_no", name="uq_agent_events_run_sequence"),
    )
    op.create_index(
        "idx_agent_events_run",
        "agent_events",
        ["run_id", "sequence_no"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_agent_events_run", table_name="agent_events")
    op.drop_table("agent_events")
    op.drop_index("idx_agent_runs_thread", table_name="agent_runs")
    op.drop_table("agent_runs")
