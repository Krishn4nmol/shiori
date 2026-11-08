"""Add users, and owners for documents and conversations.

Existing rows keep a NULL owner; creating the first admin (POST /api/auth/setup) assigns
them to that admin.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

user_role = sa.Enum("admin", "member", name="user_role")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(500), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.add_column(
        "documents",
        sa.Column("uploaded_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.add_column(
        "conversations",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE")),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_column("conversations", "user_id")
    op.drop_column("documents", "uploaded_by_id")
    op.drop_table("users")
    user_role.drop(op.get_bind(), checkfirst=True)
