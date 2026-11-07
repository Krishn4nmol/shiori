"""Add keyword search to chunks (EN words + JA bigrams in a GIN-indexed tsvector).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

from app.retrieval.tokenize import search_text

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("search_text", sa.Text(), nullable=False, server_default=""))
    op.add_column(
        "chunks",
        sa.Column(
            "search_tsv",
            TSVECTOR(),
            sa.Computed("array_to_tsvector(string_to_array(search_text, ' '))", persisted=True),
        ),
    )
    op.create_index("ix_chunks_search_tsv", "chunks", ["search_tsv"], postgresql_using="gin")

    # Backfill chunks ingested before this migration (same input as the pipeline uses).
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT c.id, d.title, c.section, c.text FROM chunks c "
            "JOIN documents d ON d.id = c.document_id"
        )
    ).all()
    for row in rows:
        source = "\n".join(x for x in (row.title, row.section, row.text) if x)
        conn.execute(
            sa.text("UPDATE chunks SET search_text = :t WHERE id = :id"),
            {"t": search_text(source), "id": row.id},
        )


def downgrade() -> None:
    op.drop_index("ix_chunks_search_tsv", table_name="chunks")
    op.drop_column("chunks", "search_tsv")
    op.drop_column("chunks", "search_text")
