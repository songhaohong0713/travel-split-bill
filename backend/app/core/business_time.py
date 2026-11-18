"""Request-local calendar shared by ledgers and historical rate validation."""

from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

client_timezone_offset: ContextVar[int] = ContextVar(
    "ledger_timezone_offset", default=0
)


def business_today(now=None):
    return (
        ((now or datetime.now(UTC)) - timedelta(minutes=client_timezone_offset.get()))
        .date()
        .isoformat()
    )
