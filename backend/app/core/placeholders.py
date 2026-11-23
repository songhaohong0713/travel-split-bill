"""Placeholder members: people in a trip who do not use the app.

A placeholder is an ordinary member id that nobody can log in as. Everything
here is pure: it takes a ledger snapshot and returns what has to be written,
so the local path and the SQL functions apply the very same rewrite.

Claiming replaces the placeholder's id with a real user's id inside one trip.
Ids are dict keys as often as they are values (allocation maps, plan bases),
and expense payloads are stored as sent, so the rewrite walks whole documents
instead of trusting a list of known fields.
"""

from copy import deepcopy
from decimal import Decimal

from app.core.ledger import FORMAT_VERSION, amount


class ClaimRefused(ValueError):
    """The trip holds data a claim cannot rewrite safely."""


def mentions(value, member):
    """Whether an id occurs anywhere in a document, as a key or a value."""
    if isinstance(value, dict):
        return any(
            key == member or mentions(item, member) for key, item in value.items()
        )
    if isinstance(value, list):
        return any(mentions(item, member) for item in value)
    return value == member


def renamed(value, old, new):
    """A copy of a document with one member id replaced by another."""
    if isinstance(value, dict):
        if old in value and new in value:
            raise ClaimRefused("认领者已出现在这段旅行的数据里")
        return {
            (new if key == old else key): renamed(item, old, new)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [renamed(item, old, new) for item in value]
    return new if isinstance(value, str) and value == old else value


def _without_participant(payload, member):
    """An expense that only lists the member, without owing or paying anything."""
    return {
        key: [person for person in value if person != member]
        if key == "participants" and isinstance(value, list)
        else value
        for key, value in payload.items()
    }


def removal(snapshot, member):
    """What deleting an unused placeholder writes, or None while it is in use.

    The app lists every member of the trip on each expense, so merely being
    listed does not count: the member must pay, owe (even zero), receive, or
    appear in a settlement, a mark or a plan, revoked ones included, because
    settlement history is never removed.
    """
    if mentions(snapshot["state"], member):
        return None
    expenses = []
    for record in snapshot["expenses"]:
        payload = record["payload"]
        stripped = _without_participant(payload, member)
        if mentions(stripped, member):
            return None
        if stripped != payload:
            expenses.append({"id": record["id"], "payload": stripped})
    return {"expenses": expenses}


def _is_pair_format(record):
    return record.get("format_version") != FORMAT_VERSION


def _negated(currency, rows):
    return [
        {**row, "amount": amount(currency, -Decimal(row["amount"]))} for row in rows
    ]


def _keep_pair_view(record, old, new):
    """Keep a two-member record meaning the same after one of its ids changed.

    Such a record stores one signed number per day, read as the net of the
    smaller id of its own payer/recipient pair. When the new id sorts on the
    other side of the counterpart, the numbers are negated so that every
    reader, the deployed client included, still sees the same money.
    """
    pair = (record.get("from_participant_id"), record.get("to_participant_id"))
    if new not in pair:
        return record
    if None in pair:
        raise ClaimRefused("这段旅行有缺少付款方或收款方的旧结清记录")
    other = pair[0] if pair[1] == new else pair[1]
    if (old < other) == (new < other):
        return record
    flipped = {
        **record,
        "allocations": _negated(record["currency"], record["allocations"]),
    }
    if "freeze_basis" in record:
        flipped["freeze_basis"] = _negated(record["currency"], record["freeze_basis"])
    return flipped


def claim(snapshot, old, new):
    """Everything a claim writes: `old` becomes `new` throughout one trip.

    Returns the expenses whose payload changes, the new ledger state, and the
    settlements whose projection rows must follow. Each person's daily net,
    balance and every settlement amount are the same before and after.
    """
    if new in snapshot["members"]:
        raise ClaimRefused("认领者已经是这段旅行的成员")
    if old not in snapshot["members"]:
        raise ClaimRefused("这位成员已不在这段旅行里")
    expenses = []
    for record in snapshot["expenses"]:
        if mentions(record["payload"], new):
            raise ClaimRefused("认领者已出现在这段旅行的数据里")
        payload = renamed(record["payload"], old, new)
        if payload != record["payload"]:
            expenses.append({"id": record["id"], "payload": payload})
    before = snapshot["state"]
    if mentions(before, new):
        raise ClaimRefused("认领者已出现在这段旅行的数据里")
    if any(
        _is_pair_format(payment)
        and None
        in (payment.get("from_participant_id"), payment.get("to_participant_id"))
        for payment in before.get("payments", [])
    ):
        # Such a record is read through the trip's first two members, which a
        # new id can change. No writer produces one; refuse rather than guess.
        raise ClaimRefused("这段旅行有缺少付款方或收款方的旧结清记录")
    state = renamed(deepcopy(before), old, new)
    state["payments"] = [
        _keep_pair_view(payment, old, new) if _is_pair_format(payment) else payment
        for payment in state.get("payments", [])
    ]
    if "payer_marks" in state:
        state["payer_marks"] = [
            _keep_pair_view(mark, old, new) if "payer_allocations" not in mark else mark
            for mark in state["payer_marks"]
        ]
    payments = [
        {
            "id": after["id"],
            "from_id": after.get("from_participant_id"),
            "to_id": after.get("to_participant_id"),
            "payload": after,
            "allocations": after["allocations"],
        }
        for original, after in zip(
            before.get("payments", []), state["payments"], strict=True
        )
        if original != after
    ]
    return {
        "expenses": expenses,
        "state": state,
        "payments": payments,
        "state_changed": state != before,
    }
