"""Repair collaboration schema and add queryable payment projections."""

import json
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "20261022_07"
down_revision = "20261021_06"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    existing = set(sa.inspect(connection).get_table_names())
    if "trip_members" not in existing:
        op.create_table(
            "trip_members",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "trip_id", sa.String(36), sa.ForeignKey("trips.id"), nullable=False
            ),
            sa.Column(
                "user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False
            ),
            sa.Column(
                "joined_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint("trip_id", "user_id", name="uq_trip_members_trip_user"),
        )
        op.create_index("ix_trip_members_trip_id", "trip_members", ["trip_id"])
        op.create_index("ix_trip_members_user_id", "trip_members", ["user_id"])
    if "trip_invites" not in existing:
        op.create_table(
            "trip_invites",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "trip_id", sa.String(36), sa.ForeignKey("trips.id"), nullable=False
            ),
            sa.Column(
                "creator_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False
            ),
            sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("used_at", sa.DateTime()),
            sa.Column("revoked_at", sa.DateTime()),
        )
    metadata = sa.MetaData()
    members = sa.Table("trip_members", metadata, autoload_with=connection)
    trips = sa.Table("trips", metadata, autoload_with=connection)
    ledgers = sa.Table("trip_ledgers", metadata, autoload_with=connection)
    for trip in connection.execute(sa.select(trips.c.id, trips.c.owner_id)):
        member = connection.execute(
            sa.select(members.c.id).where(
                members.c.trip_id == trip.id, members.c.user_id == trip.owner_id
            )
        ).first()
        if not member:
            connection.execute(
                members.insert().values(
                    id=str(uuid4()), trip_id=trip.id, user_id=trip.owner_id
                )
            )
    op.create_table(
        "payment_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "trip_id",
            sa.String(36),
            sa.ForeignKey("trips.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(20, 2), nullable=False),
        sa.Column("from_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("to_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.CheckConstraint("amount >= 0", name="ck_payment_amount_nonnegative"),
        sa.CheckConstraint("from_id <> to_id", name="ck_payment_distinct_people"),
    )
    op.create_table(
        "payment_allocations",
        sa.Column(
            "payment_id",
            sa.String(36),
            sa.ForeignKey("payment_records.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("occurred_at", sa.Date(), primary_key=True),
        sa.Column("amount", sa.Numeric(20, 2), nullable=False),
    )
    op.create_index("ix_payment_records_trip_id", "payment_records", ["trip_id"])
    op.create_index(
        "ix_payment_records_trip_created",
        "payment_records",
        ["trip_id", "created_at", "id"],
    )
    op.create_index(
        "ix_expenses_trip_date_id", "expenses", ["trip_id", "occurred_at", "id"]
    )
    op.create_index("ix_expenses_trip_id_id", "expenses", ["trip_id", "id"])
    payments = sa.Table("payment_records", metadata, autoload_with=connection)
    allocations = sa.Table("payment_allocations", metadata, autoload_with=connection)
    for ledger in connection.execute(sa.select(ledgers)).mappings():
        state = ledger["state_json"]
        if isinstance(state, str):
            state = json.loads(state)
        for p in state.get("payments", []):
            connection.execute(
                payments.insert().values(
                    id=p["id"],
                    trip_id=ledger["trip_id"],
                    currency=p["currency"],
                    amount=Decimal(p["amount"]),
                    from_id=p["from_participant_id"],
                    to_id=p["to_participant_id"],
                    created_by=p["created_by"],
                    created_at=datetime.fromisoformat(p["created_at"]),
                    revoked_at=datetime.fromisoformat(p["revoked_at"])
                    if p.get("revoked_at")
                    else None,
                    payload_json=p,
                )
            )
            for a in p["allocations"]:
                connection.execute(
                    allocations.insert().values(
                        payment_id=p["id"],
                        occurred_at=date.fromisoformat(a["date"]),
                        amount=Decimal(a["amount"]),
                    )
                )
    expenses = sa.Table("expenses", metadata, autoload_with=connection)
    historic_ids = (
        connection.execute(sa.select(expenses.c.trip_id).distinct()).scalars().all()
    )
    for trip_id in historic_ids:
        ledger = (
            connection.execute(sa.select(ledgers).where(ledgers.c.trip_id == trip_id))
            .mappings()
            .first()
        )
        state = ledger["state_json"] if ledger else {"closed_dates": [], "payments": []}
        if isinstance(state, str):
            state = json.loads(state)
        if (
            state.get("payments")
            or state.get("closed_dates")
            or state.get("history_confirmation")
        ):
            continue
        state = {
            **state,
            "history_confirmation": {"status": "pending", "source": "migration"},
        }
        if ledger:
            connection.execute(
                ledgers.update()
                .where(ledgers.c.trip_id == trip_id)
                .values(revision=ledger["revision"] + 1, state_json=state)
            )
        else:
            connection.execute(
                ledgers.insert().values(trip_id=trip_id, revision=1, state_json=state)
            )


def downgrade():
    op.drop_index("ix_expenses_trip_id_id", table_name="expenses")
    op.drop_index("ix_expenses_trip_date_id", table_name="expenses")
    op.drop_table("payment_allocations")
    op.drop_table("payment_records")
    # Keep collaboration tables and JSON audit history: they may predate this revision.
