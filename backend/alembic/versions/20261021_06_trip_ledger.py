"""Persistent daily settlement and payment history."""

import sqlalchemy as sa
from alembic import op

revision = "20261021_06"
down_revision = "20260913_05"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trips", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column("trips", sa.Column("end_date", sa.Date(), nullable=True))
    op.create_table(
        "trip_ledgers",
        sa.Column(
            "trip_id",
            sa.String(36),
            sa.ForeignKey("trips.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("trip_ledgers")
    op.drop_column("trips", "end_date")
    op.drop_column("trips", "start_date")
