"""Members who do not use the app: a flag and who added them."""

import sqlalchemy as sa
from alembic import op

revision = "20261123_14"
down_revision = "20261122_13"
branch_labels = None
depends_on = None


def upgrade():
    # Every existing member is a real one.
    with op.batch_alter_table("trip_members") as batch:
        batch.add_column(
            sa.Column(
                "is_placeholder",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("added_by", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_trip_members_added_by_users", "users", ["added_by"], ["id"]
        )


def downgrade():
    with op.batch_alter_table("trip_members") as batch:
        batch.drop_constraint("fk_trip_members_added_by_users", type_="foreignkey")
        batch.drop_column("added_by")
        batch.drop_column("is_placeholder")
