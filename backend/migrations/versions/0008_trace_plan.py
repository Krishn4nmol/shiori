"""Store the agent's plan on traces.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("traces", sa.Column("plan", JSONB()))


def downgrade() -> None:
    op.drop_column("traces", "plan")
