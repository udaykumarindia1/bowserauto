"""Add short-lived phone device pairing requests.

Revision ID: b7f2a90d5c11
Revises: e4f9a37c6112
Create Date: 2026-10-16 12:30:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7f2a90d5c11"
down_revision = "e4f9a37c6112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_pairings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("device_name", sa.String(length=100), nullable=False),
        sa.Column("user_agent", sa.String(length=400), nullable=False),
        sa.Column("claim_ip", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index("ix_device_pairings_user_id", "device_pairings", ["user_id"])
    op.create_index("ix_device_pairings_organization_id", "device_pairings", ["organization_id"])
    op.create_index(
        "ix_device_pairings_user_status_expiry",
        "device_pairings",
        ["user_id", "status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_device_pairings_user_status_expiry", table_name="device_pairings")
    op.drop_index("ix_device_pairings_organization_id", table_name="device_pairings")
    op.drop_index("ix_device_pairings_user_id", table_name="device_pairings")
    op.drop_table("device_pairings")
