"""Store a user-selected display name without changing accounting identity."""

import sqlalchemy as sa
from alembic import op

revision = "20261027_08"
down_revision = "20261022_07"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("nickname", sa.String(40), nullable=True))


def downgrade():
    op.drop_column("users", "nickname")
