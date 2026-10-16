"""Add persistent, allowlisted task schedules.

Revision ID: e4f9a37c6112
Revises: c3d1f84b5cc6
Create Date: 2026-10-16 12:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e4f9a37c6112"
down_revision = "c3d1f84b5cc6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_schedules",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("platform_key", sa.String(length=60), nullable=False),
        sa.Column("browser_session_id", sa.Uuid(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column(
            "config", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("repeat_interval_minutes", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_task_id", sa.Uuid(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["browser_session_id"], ["browser_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["last_task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_schedules_organization_id", "task_schedules", ["organization_id"])
    op.create_index("ix_task_schedules_due", "task_schedules", ["enabled", "next_run_at"])


def downgrade() -> None:
    op.drop_index("ix_task_schedules_due", table_name="task_schedules")
    op.drop_index("ix_task_schedules_organization_id", table_name="task_schedules")
    op.drop_table("task_schedules")
