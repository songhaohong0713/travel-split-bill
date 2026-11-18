"""Persist failed object deletions separately from trip metadata."""

import sqlalchemy as sa
from alembic import op

revision = "20261031_09"
down_revision = "20261027_08"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "receipt_cleanup",
        sa.Column("object_key", sa.String(255), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade():
    op.drop_table("receipt_cleanup")
