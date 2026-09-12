"""add receipt image and OCR job records

Revision ID: 20260912_04
Revises: 20260912_03
"""

import sqlalchemy as sa
from alembic import op

revision = "20260912_04"
down_revision = "20260912_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "receipt_images",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("trip_id", sa.String(36), sa.ForeignKey("trips.id"), nullable=False),
        sa.Column("object_key", sa.String(255), unique=True, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
    )
    op.create_table(
        "receipt_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "image_id",
            sa.String(36),
            sa.ForeignKey("receipt_images.id"),
            unique=True,
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("receipt_jobs")
    op.drop_table("receipt_images")
