"""memory facts

Revision ID: c3d1f84b5cc6
Revises: b2c0e73a2bb4
Create Date: 2026-10-15 09:00:00+00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3d1f84b5cc6"
down_revision = "b2c0e73a2bb4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_facts",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("memory_facts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_memory_facts_organization_id"), ["organization_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_memory_facts_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("memory_facts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_memory_facts_user_id"))
        batch_op.drop_index(batch_op.f("ix_memory_facts_organization_id"))
    op.drop_table("memory_facts")
