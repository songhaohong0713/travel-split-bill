"""A trip is settled every day or once, after its end date."""

import sqlalchemy as sa
from alembic import op

revision = "20261122_13"
down_revision = "20261119_12"
branch_labels = None
depends_on = None


def upgrade():
    # Existing trips become daily; inserts that do not name the column too.
    op.add_column(
        "trips",
        sa.Column(
            "settlement_cadence",
            sa.String(8),
            nullable=False,
            server_default="daily",
        ),
    )


def downgrade():
    with op.batch_alter_table("trips") as batch:
        batch.drop_column("settlement_cadence")
