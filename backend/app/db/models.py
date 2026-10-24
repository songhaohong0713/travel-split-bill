from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    wechat_openid_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    default_currency: Mapped[str] = mapped_column(String(3))
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    latest_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TripMember(Base):
    __tablename__ = "trip_members"
    __table_args__ = (
        UniqueConstraint("trip_id", "user_id", name="uq_trip_members_trip_user"),
    )
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TripLedger(Base):
    __tablename__ = "trip_ledgers"
    trip_id: Mapped[str] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=0)
    state_json: Mapped[dict[str, object]] = mapped_column(JSON)


class TripInvite(Base):
    __tablename__ = "trip_invites"
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime())
    used_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class PaymentRecord(Base):
    """Query projection of the immutable payment entry in trip_ledgers."""

    __tablename__ = "payment_records"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_payment_amount_nonnegative"),
        CheckConstraint("from_id <> to_id", name="ck_payment_distinct_people"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trip_id: Mapped[str] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    from_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    to_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payload_json: Mapped[dict[str, object]] = mapped_column(JSON)


class PaymentAllocationRecord(Base):
    __tablename__ = "payment_allocations"
    payment_id: Mapped[str] = mapped_column(
        ForeignKey("payment_records.id", ondelete="CASCADE"), primary_key=True
    )
    occurred_at: Mapped[date] = mapped_column(Date, primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2))


class SettlementVersion(Base):
    __tablename__ = "settlement_versions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    result_json: Mapped[dict[str, object]] = mapped_column(JSON)
    public_json: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ShareLink(Base):
    __tablename__ = "share_links"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class ReceiptImage(Base):
    __tablename__ = "receipt_images"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    object_key: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")


class ReceiptJob(Base):
    __tablename__ = "receipt_jobs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    image_id: Mapped[str] = mapped_column(ForeignKey("receipt_images.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    candidates_json: Mapped[list[str] | list[dict[str, str]] | None] = mapped_column(
        JSON, nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ExpenseRecord(Base):
    __tablename__ = "expenses"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    occurred_at: Mapped[date] = mapped_column(Date)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("owner_id", "key", name="uq_owner_idempotency_key"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    key: Mapped[str] = mapped_column(String(36))
    status_code: Mapped[int] = mapped_column(Integer)
    response_json: Mapped[str] = mapped_column(Text)
