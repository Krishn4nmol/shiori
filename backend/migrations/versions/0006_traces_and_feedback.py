"""Add traces (one per question or search) and feedback on answers.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "traces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="SET NULL")
        ),
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("search_query", sa.Text()),
        sa.Column("answered", sa.Boolean(), nullable=False),
        sa.Column("candidates", JSONB(), nullable=False),
        sa.Column("n_candidates", sa.Integer(), nullable=False),
        sa.Column("filtered_out", sa.Integer(), nullable=False),
        sa.Column("best_rerank_score", sa.Float()),
        sa.Column("model", sa.String(100)),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("timings_ms", JSONB(), nullable=False),
        sa.Column("prompt_version", sa.String(20)),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_traces_created_at", "traces", ["created_at"])
    op.add_column("messages", sa.Column("feedback", sa.SmallInteger()))
    op.add_column("messages", sa.Column("feedback_note", sa.Text()))


def downgrade() -> None:
    op.drop_column("messages", "feedback_note")
    op.drop_column("messages", "feedback")
    op.drop_table("traces")
