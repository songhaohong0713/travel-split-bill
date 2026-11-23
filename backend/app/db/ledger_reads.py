from copy import deepcopy

from sqlalchemy import select

from app.core.cadence import CADENCE_TRIP, SNAPSHOT_CADENCE, SNAPSHOT_END_DATE
from app.db.models import ExpenseRecord, Trip, TripLedger, TripMember


def cadence_snapshot(trip):
    """The two snapshot keys that decide when a trip's days may be settled.

    Same keys and values as the SQL tsb_ledger_snapshot: the end date is an
    ISO string, and present only under the trip cadence so that editing the
    dates of a daily trip leaves its previews valid.
    """
    whole = trip.settlement_cadence == CADENCE_TRIP
    return {
        SNAPSHOT_CADENCE: trip.settlement_cadence,
        SNAPSHOT_END_DATE: str(trip.end_date) if whole and trip.end_date else None,
    }


def trip_snapshot(session, trip):
    """The ledger snapshot of one trip, as tsb_ledger_snapshot returns it."""
    ledger = session.get(TripLedger, trip.id)
    records = session.scalars(
        select(ExpenseRecord)
        .where(ExpenseRecord.trip_id == trip.id)
        .order_by(ExpenseRecord.id)
    ).all()
    members = sorted(
        session.scalars(
            select(TripMember.user_id).where(TripMember.trip_id == trip.id)
        ).all()
    )
    return {
        "currency": trip.default_currency,
        "timezone_offset_minutes": trip.timezone_offset_minutes,
        **cadence_snapshot(trip),
        "members": members,
        "revision": ledger.revision if ledger else 0,
        "state": deepcopy(ledger.state_json) if ledger else {"payments": []},
        "expenses": [
            {
                "id": r.id,
                "revision": r.revision,
                "occurred_at": str(r.occurred_at),
                "payload": r.payload_json,
            }
            for r in records
        ],
    }


def member_snapshots(session, user_id):
    """Four set queries regardless of how many trips are in the homepage."""
    trips = session.scalars(
        select(Trip)
        .join(TripMember)
        .where(TripMember.user_id == user_id)
        .order_by(Trip.id)
    ).all()
    ids = [trip.id for trip in trips]
    if not ids:
        return []
    ledgers = {
        l.trip_id: l
        for l in session.scalars(
            select(TripLedger).where(TripLedger.trip_id.in_(ids))
        ).all()
    }
    members = {trip_id: [] for trip_id in ids}
    for member in session.scalars(
        select(TripMember)
        .where(TripMember.trip_id.in_(ids))
        .order_by(TripMember.user_id)
    ).all():
        members[member.trip_id].append(member.user_id)
    expenses = {trip_id: [] for trip_id in ids}
    for record in session.scalars(
        select(ExpenseRecord)
        .where(ExpenseRecord.trip_id.in_(ids))
        .order_by(ExpenseRecord.id)
    ).all():
        expenses[record.trip_id].append(
            {
                "id": record.id,
                "revision": record.revision,
                "occurred_at": str(record.occurred_at),
                "payload": record.payload_json,
            }
        )
    return [
        (
            trip.id,
            {
                "currency": trip.default_currency,
                "timezone_offset_minutes": trip.timezone_offset_minutes,
                **cadence_snapshot(trip),
                "members": members[trip.id],
                "expenses": expenses[trip.id],
                "revision": ledgers[trip.id].revision if trip.id in ledgers else 0,
                "state": deepcopy(ledgers[trip.id].state_json)
                if trip.id in ledgers
                else {"payments": []},
            },
        )
        for trip in trips
    ]
