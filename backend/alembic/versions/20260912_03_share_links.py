"""add expiring public share links

Revision ID: 20260912_03
Revises: 20260912_02
"""

import sqlalchemy as sa
from alembic import op

revision = "20260912_03"
down_revision = "20260912_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "share_links",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "trip_id", sa.String(length=36), sa.ForeignKey("trips.id"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_share_links_token_hash", "share_links", ["token_hash"], unique=True
    )
    op.create_index("ix_share_links_trip_id", "share_links", ["trip_id"])


def downgrade() -> None:
    op.drop_table("share_links")
