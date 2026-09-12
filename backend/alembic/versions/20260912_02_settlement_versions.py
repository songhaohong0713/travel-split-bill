"""add settlement version snapshots

Revision ID: 20260912_02
Revises: 20260912_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20260912_02"
down_revision = "20260912_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("trips") as batch:
        batch.add_column(
            sa.Column("latest_version_id", sa.String(length=36), nullable=True)
        )
    op.create_table(
        "settlement_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "trip_id", sa.String(length=36), sa.ForeignKey("trips.id"), nullable=False
        ),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("public_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_settlement_versions_trip_id", "settlement_versions", ["trip_id"]
    )


def downgrade() -> None:
    op.drop_table("settlement_versions")
    with op.batch_alter_table("trips") as batch:
        batch.drop_column("latest_version_id")
