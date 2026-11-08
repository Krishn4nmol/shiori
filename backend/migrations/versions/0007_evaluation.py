"""Add evaluation: test questions, runs and per-question results.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("expected_answer", sa.Text()),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("evidence", sa.Text()),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("config", JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("metrics", JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "eval_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "run_id", sa.Uuid(), sa.ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "question_id",
            sa.Uuid(),
            sa.ForeignKey("eval_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relevant_rank", sa.Integer()),
        sa.Column("kept_relevant", sa.Boolean(), nullable=False),
        sa.Column("answered", sa.Boolean(), nullable=False),
        sa.Column("candidates", JSONB(), nullable=False),
        sa.Column("answer", sa.Text()),
        sa.Column("judge", JSONB()),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_eval_results_run_id", "eval_results", ["run_id"])


def downgrade() -> None:
    op.drop_table("eval_results")
    op.drop_table("eval_runs")
    op.drop_table("eval_questions")
