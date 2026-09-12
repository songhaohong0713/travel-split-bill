"""create identity and travel ledger tables

Revision ID: 20260912_01
Revises:
Create Date: 2026-09-12
"""

import sqlalchemy as sa
from alembic import op

revision = "20260912_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "wechat_openid_hash", sa.String(length=64), unique=True, nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_table(
        "trips",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "owner_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("default_currency", sa.String(length=3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_trips_owner_id", "trips", ["owner_id"])
    op.create_table(
        "expenses",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "trip_id", sa.String(length=36), sa.ForeignKey("trips.id"), nullable=False
        ),
        sa.Column(
            "owner_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.Date(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_expenses_trip_id", "expenses", ["trip_id"])
    op.create_index("ix_expenses_owner_id", "expenses", ["owner_id"])
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "owner_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("key", sa.String(length=36), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("owner_id", "key", name="uq_owner_idempotency_key"),
    )
    op.create_index("ix_idempotency_keys_owner_id", "idempotency_keys", ["owner_id"])


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_table("expenses")
    op.drop_table("trips")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
