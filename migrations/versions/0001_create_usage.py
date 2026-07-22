"""create usage table

The columns mirror llmgateway.store.UsageRow exactly (same names, types,
nullability, and the two indexes SQLAlchemy would emit for the model), so
`alembic upgrade head` and the store's create_all() produce the same schema —
there's no drift for a reviewer to trip over.

Revision ID: 0001_create_usage
Revises:
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_create_usage"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("rate_limited", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_model", "usage", ["model"])
    op.create_index("ix_usage_created_at", "usage", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_created_at", table_name="usage")
    op.drop_index("ix_usage_model", table_name="usage")
    op.drop_table("usage")
