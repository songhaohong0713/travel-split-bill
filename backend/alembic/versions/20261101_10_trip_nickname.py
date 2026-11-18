"""Trip-scoped member display names; identities and permissions are unchanged."""

import sqlalchemy as sa
from alembic import op

revision = "20261101_10"
down_revision = "20261031_09"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trip_members", sa.Column("nickname", sa.String(40), nullable=True))
    op.add_column(
        "receipt_cleanup",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("receipt_cleanup", "attempts")
    op.drop_column("trip_members", "nickname")
