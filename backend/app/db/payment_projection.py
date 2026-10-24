from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.db.models import PaymentAllocationRecord, PaymentRecord


def sync_payments(session, trip_id, state):
    existing = {
        row.id: row
        for row in session.scalars(
            select(PaymentRecord).where(PaymentRecord.trip_id == trip_id)
        ).all()
    }
    ids = {p["id"] for p in state["payments"]}
    if not set(existing).issubset(ids) or len(ids) != len(state["payments"]):
        raise ValueError("历史转账不能删除或重复")
    for payment in state["payments"]:
        row = existing.get(payment["id"])
        if row is None:
            row = PaymentRecord(
                id=payment["id"],
                trip_id=trip_id,
                currency=payment["currency"],
                amount=Decimal(payment["amount"]),
                from_id=payment["from_participant_id"],
                to_id=payment["to_participant_id"],
                created_by=payment["created_by"],
                created_at=datetime.fromisoformat(payment["created_at"]),
                payload_json=payment,
            )
            session.add(row)
            session.flush()
            allocations = payment["allocations"]
            total = sum((Decimal(a["amount"]) for a in allocations), Decimal(0))
            if abs(total) != Decimal(payment["amount"]):
                raise ValueError("转账与逐日抵扣金额不一致")
            for allocation in allocations:
                session.add(
                    PaymentAllocationRecord(
                        payment_id=row.id,
                        occurred_at=date.fromisoformat(allocation["date"]),
                        amount=Decimal(allocation["amount"]),
                    )
                )
        else:
            immutable = lambda p: {
                k: v for k, v in p.items() if k not in {"revoked_at", "revoked_by"}
            }
            if immutable(row.payload_json) != immutable(payment):
                raise ValueError("历史转账金额与快照不能修改")
            if row.payload_json.get("revoked_at") == payment.get("revoked_at"):
                continue
        row.revoked_at = (
            datetime.fromisoformat(payment["revoked_at"])
            if payment.get("revoked_at")
            else None
        )
        row.payload_json = payment
