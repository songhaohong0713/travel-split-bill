"""add OCR job candidates and retry metadata

Revision ID: 20260913_05
Revises: 20260912_04
"""

import sqlalchemy as sa
from alembic import op

revision = "20260913_05"
down_revision = "20260912_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("receipt_jobs") as batch:
        batch.add_column(
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("candidates_json", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("error_code", sa.String(length=80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("receipt_jobs") as batch:
        batch.drop_column("error_code")
        batch.drop_column("candidates_json")
        batch.drop_column("attempts")
