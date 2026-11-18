"""Offsets settle one member's cancelling days and have no payer."""

import sqlalchemy as sa
from alembic import op

revision = "20261119_12"
down_revision = "20261101_11"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("payment_records") as batch:
        batch.alter_column("from_id", existing_type=sa.String(36), nullable=True)


def downgrade():
    with op.batch_alter_table("payment_records") as batch:
        batch.alter_column("from_id", existing_type=sa.String(36), nullable=False)
