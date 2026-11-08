"""Track each paper's knowledge-graph build.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("graph_status", sa.String(20), nullable=False, server_default="none"),
    )
    op.add_column(
        "documents", sa.Column("graph_entities", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "documents", sa.Column("graph_relations", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("documents", sa.Column("graph_error", sa.Text()))


def downgrade() -> None:
    for column in ("graph_error", "graph_relations", "graph_entities", "graph_status"):
        op.drop_column("documents", column)
