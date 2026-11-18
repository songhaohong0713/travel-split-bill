"""Fixed trip calendar and conservative receipt retention origin."""

import sqlalchemy as sa
from alembic import op

revision = "20261101_11"
down_revision = "20261101_10"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "trips", sa.Column("timezone_offset_minutes", sa.Integer(), nullable=True)
    )
    mode = "always" if op.get_bind().dialect.name == "sqlite" else "auto"
    with op.batch_alter_table("receipt_images", recreate=mode) as batch:
        batch.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )


def downgrade():
    op.drop_column("receipt_images", "created_at")
    op.drop_column("trips", "timezone_offset_minutes")
