from copy import deepcopy

from sqlalchemy import select

from app.db.models import ExpenseRecord, Trip, TripLedger, TripMember


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
                "members": members[trip.id],
                "expenses": expenses[trip.id],
                "revision": ledgers[trip.id].revision if trip.id in ledgers else 0,
                "state": deepcopy(ledgers[trip.id].state_json)
                if trip.id in ledgers
                else {"closed_dates": [], "payments": []},
            },
        )
        for trip in trips
    ]
