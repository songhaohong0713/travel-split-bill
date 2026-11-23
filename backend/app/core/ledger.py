"""Daily balances reuse the money engine; payments offset, never rewrite, expenses.

Settlement records exist in exactly two stored formats:

* legacy (no ``format_version``): ``allocations`` and ``freeze_basis`` hold one
  signed number per date, read from the point of view of the record's own
  sorted payer/recipient pair. Two-member trips keep writing this format.
* ``format_version == 2``: stored per member. ``allocations`` is what the
  recipient's net was reduced by on each date, ``payer_allocations`` the same
  for the payer, and every ``freeze_basis`` row names its ``participant_id``.
"""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.api.v1.settlements import ExpenseInput, _grouped_payload
from app.core.business_time import (
    business_today,
    client_timezone_offset,  # noqa: F401 - compatibility re-export
)
from app.core.cadence import (
    CADENCE_DAILY,
    CADENCE_TRIP,
    SNAPSHOT_CADENCE,
    SNAPSHOT_END_DATE,
)
from app.core.money import quantized_money
from app.core.settlement import minimum_transfers

FORMAT_VERSION = 2
# Minutes behind UTC, as trips store it: UTC+8.
DEFAULT_TIMEZONE_OFFSET_MINUTES = -480
VERSIONED = {"format_version": FORMAT_VERSION}
TRANSFER_IDENTITY = (
    "currency",
    "amount",
    "from_participant_id",
    "to_participant_id",
    "allocations",
    "payer_allocations",
)


def snapshot_token(snapshot):
    return hashlib.sha256(
        json.dumps(
            snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def amount(currency, value):
    return quantized_money(currency, value).amount


def trip_today(snapshot, now=None):
    if "timezone_offset_minutes" not in snapshot:
        return business_today(now) if now else business_today()
    # Trips from before timezones were stored have none until their creator
    # sets one; until then their days are Beijing days for every member.
    offset = snapshot["timezone_offset_minutes"]
    if offset is None:
        offset = DEFAULT_TIMEZONE_OFFSET_MINUTES
    return ((now or datetime.now(UTC)) - timedelta(minutes=offset)).date().isoformat()


def awaits_trip_end(snapshot, today):
    """Whether a trip settled as a whole is still before its settlement day.

    Settlement opens the day after the end date, like a day opens the day
    after it. Without an end date there is no such day, so it stays closed.
    A snapshot without the key, as older SQL returns it, is a daily trip.
    """
    if snapshot.get(SNAPSHOT_CADENCE, CADENCE_DAILY) != CADENCE_TRIP:
        return False
    end = snapshot.get(SNAPSHOT_END_DATE)
    return end is None or today <= end


def _trip_end_message(snapshot):
    end = snapshot.get(SNAPSHOT_END_DATE)
    return (
        f"这段旅行是整趟结，{end} 结束后才能结清"
        if end
        else "这段旅行是整趟结，请旅行创建者先设置结束日期"
    )


def _raw_days(snapshot, records=None):
    """Per-day expense groups before any settlement is applied."""
    members = sorted(snapshot["members"])
    all_records = snapshot["expenses"] if records is None else records
    days = {}
    for day in {r["occurred_at"] for r in all_records}:
        records_of_day = [r for r in all_records if r["occurred_at"] == day]
        groups = []
        error = ""
        try:
            expenses = []
            for record in records_of_day:
                raw = record["payload"].get("expenses", [])
                if not raw:
                    raise ValueError("消费缺少分摊数据")
                for item in raw:
                    parsed = ExpenseInput.model_validate(item)
                    if not parsed.items:
                        raise ValueError("消费缺少商品金额")
                    ids = {parsed.payer_id}
                    for line in parsed.items:
                        ids.update(line.allocation)
                    for adjustment in parsed.adjustments:
                        ids.update(adjustment.allocation)
                        if adjustment.received_by:
                            ids.add(adjustment.received_by)
                    if not ids.issubset(set(members)):
                        raise ValueError("消费包含非旅行成员，请先修正付款人和分摊")
                    expenses.append(parsed.to_expense(snapshot["currency"]))
            if expenses:
                groups = _grouped_payload(tuple(members), tuple(expenses))["groups"]
        except (ValueError, ArithmeticError, TypeError, AttributeError):
            error = "消费分摊或汇率不完整，请打开消费修正"
        days[day] = {
            "date": day,
            "error": error,
            "count": len(records_of_day),
            "expense_ids": [r["id"] for r in records_of_day],
            "groups": groups,
        }
    return days


def _balances(day):
    return {
        g["currency"]: {
            member: Decimal(value["amount"])
            for member, value in g["net_by_participant"].items()
        }
        for g in day["groups"]
    }


def _legacy_net(members, value, payment):
    # Signed x belongs to the first sorted participant of this payment's pair.
    # Handwritten legacy fixtures without endpoints retain the old fallback.
    pair = (
        sorted((payment["from_participant_id"], payment["to_participant_id"]))
        if "from_participant_id" in payment and "to_participant_id" in payment
        else members[:2]
    )
    signed = Decimal(value)
    net = dict.fromkeys(members, Decimal(0))
    net.update(zip(pair, (signed, -signed)))
    return net


def _is_member_format(payment):
    return payment.get("format_version") == FORMAT_VERSION


def _deductions(members, payment):
    """(date, member, amount) taken off each member's net by one settlement."""
    if _is_member_format(payment):
        for member, key in (
            (payment["to_participant_id"], "allocations"),
            (payment["from_participant_id"], "payer_allocations"),
        ):
            for allocation in payment[key]:
                yield allocation["date"], member, Decimal(allocation["amount"])
        return
    for allocation in payment["allocations"]:
        for member, value in _legacy_net(
            members, allocation["amount"], payment
        ).items():
            yield allocation["date"], member, value


def _transfer_dates(transfer):
    return sorted(
        {
            a["date"]
            for a in transfer["allocations"] + transfer.get("payer_allocations", [])
        }
    )


def _member_dates(members, payment):
    """Dates on which a settlement moved or froze each member's balance."""
    if not _is_member_format(payment):
        dates = {
            a["date"] for a in payment["allocations"] + payment.get("freeze_basis", [])
        }
        return dict.fromkeys(members, dates)
    touched = {}
    for day, member, _ in _deductions(members, payment):
        touched.setdefault(member, set()).add(day)
    for basis in payment["freeze_basis"]:
        touched.setdefault(basis["participant_id"], set()).add(basis["date"])
    return touched


def _pending(balances, members):
    pending = []
    for currency, net in sorted(balances.items()):
        for transfer in minimum_transfers(net, currency):
            row = {
                "currency": currency,
                "amount": transfer.amount.amount,
                "from_participant_id": transfer.from_participant_id,
                "to_participant_id": transfer.to_participant_id,
            }
            if len(members) == 2:
                row["signed_amount"] = amount(currency, net[members[0]])
            pending.append(row)
    return pending


def project(snapshot):
    members = sorted(snapshot["members"])
    raw = _raw_days(snapshot)
    active = [p for p in snapshot["state"]["payments"] if not p.get("revoked_at")]
    frozen = {}
    known_currencies = {}
    active_ids = {p["id"] for p in active}
    for payment in active:
        if not set(payment.get("depends_on", [])).issubset(active_ids):
            continue
        if _is_member_format(payment):
            for basis in payment["freeze_basis"]:
                key = basis["date"], basis["currency"], basis["participant_id"]
                frozen[key] = Decimal(basis["amount"])
            continue
        if "basis_currencies" in payment:
            for day, currencies in payment["basis_currencies"].items():
                known_currencies[day] = set(currencies)
        else:
            evidence = _raw_days(snapshot, payment.get("basis_expenses", []))
            evidence_dates = {a["date"] for a in payment["allocations"]} | {
                b["date"] for b in payment.get("freeze_basis", [])
            }
            for day in evidence_dates:
                known_currencies[day] = set(
                    _balances(evidence.get(day, {"groups": []}))
                )
        if "freeze_basis" in payment:
            for basis in payment["freeze_basis"]:
                for member, value in _legacy_net(
                    members, basis["amount"], payment
                ).items():
                    frozen[basis["date"], basis["currency"], member] = value
        else:
            basis = _raw_days(snapshot, payment.get("basis_expenses", []))
            for allocation in payment["allocations"]:
                key = allocation["date"], payment["currency"]
                original = _balances(basis.get(key[0], {"groups": []})).get(key[1])
                if original is not None and original == _legacy_net(
                    members, allocation["amount"], payment
                ):
                    for member, value in original.items():
                        frozen[*key, member] = value
    for day, currencies in known_currencies.items():
        if currencies and all(
            (day, c, member) in frozen for c in currencies for member in members
        ):
            for currency in _balances(raw.get(day, {"groups": []})):
                for member in members:
                    frozen.setdefault((day, currency, member), Decimal(0))
    dates = set(raw)
    for payment in snapshot["state"]["payments"]:
        dates |= {day for day, _, _ in _deductions(members, payment)}
    balances = {d: _balances(raw.get(d, {"groups": []})) for d in dates}
    carries = {d: {} for d in dates}
    for payment in active:
        currency = payment["currency"]
        for day, member, value in _deductions(members, payment):
            if (day, currency, member) not in frozen:
                net = balances[day].setdefault(currency, {})
                net[member] = net.get(member, Decimal(0)) - value
    today = trip_today(snapshot)
    held = awaits_trip_end(snapshot, today)
    for day, currency, member in frozen:
        balances.setdefault(day, {}).setdefault(currency, {})[member] = Decimal(0)
    for (day, currency, member), original in sorted(frozen.items()):
        current = (
            _balances(raw.get(day, {"groups": []}))
            .get(currency, {})
            .get(member, Decimal(0))
        )
        delta = current - original
        if not delta:
            continue
        target = next(
            (
                d
                for d in sorted(dates)
                if d > day and (d, currency, member) not in frozen
            ),
            (date.fromisoformat(day) + timedelta(days=1)).isoformat(),
        )
        while (target, currency, member) in frozen:
            target = (date.fromisoformat(target) + timedelta(days=1)).isoformat()
        dates.add(target)
        net = balances.setdefault(target, {}).setdefault(currency, {})
        net[member] = net.get(member, Decimal(0)) + delta
        carries.setdefault(target, {})[day, currency, member] = {
            "participant_id": member,
            "source_date": day,
            "currency": currency,
            "signed_amount": delta,
        }
    days = []
    for day in sorted(dates, reverse=True):
        row = deepcopy(
            raw.get(
                day,
                {"date": day, "error": "", "count": 0, "expense_ids": [], "groups": []},
            )
        )
        row["original_totals"] = [
            {
                "currency": g["currency"],
                "amount": amount(
                    g["currency"],
                    sum(
                        (
                            Decimal(v["amount"])
                            for v in g["paid_by_participant"].values()
                        ),
                        Decimal(0),
                    ),
                ),
                "signed_amount": amount(
                    g["currency"], _balances(row)[g["currency"]][members[0]]
                ),
            }
            for g in row["groups"]
        ]
        # Two members freeze together, so one entry from the first member's
        # side loses nothing. With more members every entry names its member.
        row["carry_entries"] = [
            {
                "kind": "adjustment",
                **(
                    {}
                    if len(members) <= 2
                    else {"participant_id": entry["participant_id"]}
                ),
                "source_date": entry["source_date"],
                "currency": entry["currency"],
                "signed_amount": amount(entry["currency"], entry["signed_amount"]),
                "amount": amount(entry["currency"], abs(entry["signed_amount"])),
            }
            for entry in carries.get(day, {}).values()
            if len(members) > 2 or entry["participant_id"] == members[0]
        ]
        row["pending"] = _pending(balances[day], members)
        row["balances"] = [
            {
                "currency": currency,
                "participant_id": member,
                "amount": amount(currency, value),
            }
            for currency, net in sorted(balances[day].items())
            for member, value in sorted(net.items())
            if value
        ]
        row["settled_transfers"] = [
            p for p in active if any(d == day for d, _, _ in _deductions(members, p))
        ]
        # A member can hold a balance no transfer can match yet, so the day
        # is judged by balances, not by whether a transfer exists. While a
        # trip settled as a whole has not ended, only a day that was already
        # settled and still owes nothing keeps showing as settled.
        recording = day >= today or (
            held and (row["balances"] or not row["settled_transfers"])
        )
        row["status"] = (
            "invalid"
            if row["error"]
            else "recording"
            if recording
            else "pending"
            if row["balances"]
            else "settled"
        )
        days.append(row)
    return {
        "days": days,
        "payer_marks": snapshot["state"].get("payer_marks", []),
        "payments": snapshot["state"]["payments"],
        "revision": snapshot["revision"],
        "members": members,
        "history_status": snapshot["state"]
        .get("history_confirmation", {})
        .get("status", "not_required"),
    }


def _day_net(row, currency):
    return {
        b["participant_id"]: Decimal(b["amount"])
        for b in row["balances"]
        if b["currency"] == currency
    }


def _take(work, days, currency, member, target, totals):
    """Assign `target` of a member's balance to one transfer, oldest day first.

    Days running against the transfer are absorbed whole; the member's last
    transfer takes everything that is left so no day keeps a stray remainder.
    """
    exhaust = totals[member] == target
    left = target
    rows = []
    for day in days:
        balance = work[day].get(member, Decimal(0))
        if not balance:
            continue
        take = (
            balance
            if exhaust or (balance > 0) != (left > 0) or abs(balance) <= abs(left)
            else left
        )
        work[day][member] = balance - take
        left -= take
        rows.append({"date": day, "amount": amount(currency, take)})
        if not exhaust and not left:
            break
    totals[member] -= target
    return rows


def _plan_transfers(currency, net):
    """Fewest transfers for the part of a date range that balances out."""
    days = sorted(net)
    work = {day: dict(net[day]) for day in days}
    totals = {}
    for day in days:
        for member, value in work[day].items():
            totals[member] = totals.get(member, Decimal(0)) + value
    idle = sorted(member for member, total in totals.items() if not total)
    transfers = []
    for transfer in minimum_transfers(dict(totals), currency):
        value = Decimal(transfer.amount.amount)
        if not value:
            continue
        payee, payer = transfer.to_participant_id, transfer.from_participant_id
        transfers.append(
            {
                "currency": currency,
                "amount": transfer.amount.amount,
                "from_participant_id": payer,
                "to_participant_id": payee,
                "allocations": _take(work, days, currency, payee, value, totals),
                "payer_allocations": _take(work, days, currency, payer, -value, totals),
            }
        )
    # Days that cancel out for one member move no money, but they still have
    # to be closed: an offset any member can confirm.
    for member in idle:
        rows = _take(work, days, currency, member, Decimal(0), totals)
        if rows:
            transfers.append(
                {
                    "currency": currency,
                    "amount": amount(currency, Decimal(0)),
                    "from_participant_id": None,
                    "to_participant_id": member,
                    "allocations": rows,
                    "payer_allocations": [],
                    "offset_only": True,
                }
            )
    return transfers


def _active_payments(snapshot, plan_ids):
    return [
        p
        for p in snapshot["state"]["payments"]
        if not p.get("revoked_at") and p.get("plan_id") in plan_ids
    ]


def _open_transfers(snapshot, plan):
    settled = {
        (p["from_participant_id"], p["to_participant_id"])
        for p in _active_payments(snapshot, {plan["id"]})
    }
    return [
        t
        for t in plan["transfers"]
        if (t["from_participant_id"], t["to_participant_id"]) not in settled
    ]


def _plan_claim(snapshot, by_date, plan):
    """What a fixed plan still has to settle per day, or None once it broke.

    A plan stands while balances only moved by its own settlements. One built
    around earlier plans' open transfers (`includes`) owns just what those
    leave, so their open transfers are set aside before comparing.
    """
    currency = plan["currency"]
    members = sorted(snapshot["members"])
    claim = {
        day: {member: Decimal(value) for member, value in net.items()}
        for day, net in plan["basis"].items()
    }
    for payment in _active_payments(snapshot, {plan["id"]}):
        for day, member, value in _deductions(members, payment):
            net = claim.setdefault(day, {})
            net[member] = net.get(member, Decimal(0)) - value
    current = {
        day: _day_net(by_date[day], currency) if day in by_date else {} for day in claim
    }
    stored = {p["id"]: p for p in snapshot["state"].get("plans", [])}
    for included in plan.get("includes", []):
        if (
            included not in stored
            or _plan_claim(snapshot, by_date, stored[included]) is None
        ):
            return None
        for transfer in _open_transfers(snapshot, stored[included]):
            for day, member, value in _deductions([], transfer | VERSIONED):
                if day in current:
                    net = current[day]
                    net[member] = net.get(member, Decimal(0)) - value
    for day, net in claim.items():
        if any(
            current[day].get(member, Decimal(0)) != net.get(member, Decimal(0))
            for member in set(current[day]) | set(net)
        ):
            return None
    return {day: net for day, net in claim.items() if any(net.values())}


def _open_marks(snapshot, plan):
    return [
        m
        for m in snapshot["state"].get("payer_marks", [])
        if m.get("plan_id") == plan["id"]
        and not m.get("revoked_at")
        and not m.get("confirmed_payment_id")
    ]


def _is_backed(snapshot, plan):
    """Someone marked or confirmed a transfer of this plan and none was undone."""
    return bool(_active_payments(snapshot, {plan["id"]})) or bool(
        _open_marks(snapshot, plan)
    )


def _currency_scope(snapshot, by_date, currency, net):
    """Plans covering one currency of a range, oldest first, and conflicts.

    Open transfers of a plan someone stands behind are kept as they are and
    only what they leave is paired anew. A range that cuts through such a plan
    cannot keep it whole, so it is reported as a conflict instead.
    """
    live = set(net)
    kept, conflicts, unclaimed = [], [], None
    for plan in snapshot["state"].get("plans", []):
        if plan["currency"] != currency:
            continue
        claim = _plan_claim(snapshot, by_date, plan)
        if not claim or not set(claim) & live:
            continue
        if not _is_backed(snapshot, plan):
            # Nobody stands behind it: reused only for exactly its own range.
            unclaimed = plan if set(claim) == live else unclaimed
        elif set(claim) <= live:
            kept.append(plan)
        else:
            conflicts.append(
                {
                    "currency": currency,
                    "dates": sorted(claim),
                    "marked_by": sorted(
                        {m["marked_by"] for m in _open_marks(snapshot, plan)}
                    ),
                    "confirmed_by": sorted(
                        {
                            p["created_by"]
                            for p in _active_payments(snapshot, {plan["id"]})
                        }
                    ),
                }
            )
    if conflicts:
        kept = []
    elif not kept and unclaimed:
        kept = [unclaimed]
    rest = {day: dict(values) for day, values in net.items()}
    for plan in kept:
        for transfer in _open_transfers(snapshot, plan):
            for day, member, value in _deductions([], transfer | VERSIONED):
                rest[day][member] = rest[day].get(member, Decimal(0)) - value
    rest = {
        day: {m: v for m, v in values.items() if v}
        for day, values in rest.items()
        if any(values.values())
    }
    fresh = _plan_transfers(currency, rest)
    if fresh or not kept:
        kept.append(
            {
                "id": None,
                "currency": currency,
                "dates": sorted(rest),
                "basis": {
                    day: {m: amount(currency, v) for m, v in sorted(values.items())}
                    for day, values in rest.items()
                },
                "includes": [p["id"] for p in kept],
                "transfers": fresh,
            }
        )
    return kept, conflicts


def _selected_days(snapshot, requested_dates, ledger):
    today = trip_today(snapshot)
    held = awaits_trip_end(snapshot, today)
    selected = (
        sorted(set(requested_dates))
        if requested_dates is not None
        else sorted(d["date"] for d in ledger["days"] if d["status"] != "recording")
    )
    if not selected:
        raise ValueError(
            _trip_end_message(snapshot) if held else "今天的账单明天才能结清"
        )
    by_date = {d["date"]: d for d in ledger["days"]}
    if any(day >= today for day in selected):
        raise ValueError("今天仍在记录中，不能结清")
    if held and any(
        day in by_date and by_date[day]["status"] == "recording" for day in selected
    ):
        raise ValueError(_trip_end_message(snapshot))
    if any(
        day not in by_date or by_date[day]["status"] in ("recording", "invalid")
        for day in selected
    ):
        raise ValueError("所选日期仍在记录中或消费数据不完整")
    return selected, by_date


def _pair_groups(selected, by_date, members):
    """The two-member proposal: one signed group per currency, as before."""
    currencies = sorted(
        {p["currency"] for day in selected for p in by_date[day]["pending"]}
    )
    groups = []
    for currency in currencies:
        allocations = [
            {
                "date": day,
                "amount": next(
                    (
                        p["signed_amount"]
                        for p in by_date[day]["pending"]
                        if p["currency"] == currency
                    ),
                    "0",
                ),
            }
            for day in selected
            if any(p["currency"] == currency for p in by_date[day]["pending"])
        ]
        total = sum((Decimal(a["amount"]) for a in allocations), Decimal(0))
        groups.append(
            {
                "currency": currency,
                "amount": amount(currency, abs(total)),
                "from_participant_id": members[1] if total >= 0 else members[0],
                "to_participant_id": members[0] if total >= 0 else members[1],
                "allocations": allocations,
                "offset_only": total == 0,
            }
        )
    return groups


def settlement_scope(snapshot, requested_dates=None, *, ledger=None):
    """`payment_preview` plus the plan each currency's transfers come from."""
    ledger = project(snapshot) if ledger is None else ledger
    selected, by_date = _selected_days(snapshot, requested_dates, ledger)
    selected = [day for day in selected if by_date[day]["balances"]]
    members = ledger["members"]
    token = snapshot_token(snapshot)
    if len(members) <= 2:
        groups = _pair_groups(selected, by_date, members)
        return {
            "dates": selected,
            "groups": groups,
            "transfers": groups,
            "token": token,
            "plans": [],
        }
    plans, transfers, conflicts = [], [], []
    for currency in sorted(
        {b["currency"] for day in selected for b in by_date[day]["balances"]}
    ):
        net = {
            day: _day_net(by_date[day], currency)
            for day in selected
            if _day_net(by_date[day], currency)
        }
        kept, blocked = _currency_scope(snapshot, by_date, currency, net)
        conflicts += blocked
        for plan in kept:
            # Each row keeps a link to its plan; fixing a plan fills in the id.
            plan = {**plan, "open": []}
            plans.append(plan)
            for transfer in (
                _open_transfers(snapshot, plan) if plan["id"] else plan["transfers"]
            ):
                plan["open"].append({**transfer, "plan_id": plan["id"]})
            transfers += plan["open"]
    return {
        "dates": selected,
        "transfers": transfers,
        "conflicts": conflicts,
        "token": token,
        "plans": plans,
    }


def payment_preview(snapshot, requested_dates=None, *, ledger=None):
    scope = settlement_scope(snapshot, requested_dates, ledger=ledger)
    return {key: value for key, value in scope.items() if key != "plans"}


def find_transfer(scope, currency, payer=None, payee=None, *, offset=None, plan=None):
    """One row of a proposal: a payer/recipient pair, or a member's offset."""
    return next(
        (
            t
            for t in scope["transfers"]
            if t["currency"] == currency
            and (
                (t.get("offset_only") and t["to_participant_id"] == offset)
                if offset is not None
                else (
                    (payer is None or t["from_participant_id"] == payer)
                    and (payee is None or t["to_participant_id"] == payee)
                )
            )
            and (plan is None or t.get("plan_id") == plan)
        ),
        None,
    )


def fix_plans(state, scope, user_id):
    """Store the whole proposal of a range the first time someone acts on it."""
    plans = state.setdefault("plans", [])
    for plan in scope["plans"]:
        if plan["id"] is not None or not plan["transfers"]:
            continue
        plan["id"] = str(uuid4())
        for transfer in plan["open"]:
            transfer["plan_id"] = plan["id"]
        plans.append(
            {
                **{k: deepcopy(v) for k, v in plan.items() if k != "open"},
                "created_by": user_id,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )


def drop_broken_plans(snapshot, state):
    """Forget fixed plans that an expense change or outside settlement broke."""
    if not state.get("plans"):
        return
    by_date = {d["date"]: d for d in project(snapshot)["days"]}
    state["plans"] = [
        p for p in state["plans"] if _plan_claim(snapshot, by_date, p) is not None
    ]


def same_transfer(left, right):
    return all(left.get(key) == right.get(key) for key in TRANSFER_IDENTITY)


def mark_matches(mark, transfer):
    """Whether a payer's mark still stands for a transfer proposed now.

    A mark made while the trip had two members knows neither plans nor
    per-member allocations. It keeps standing after more members join as long
    as the same pair still owes the same amount for the same days.
    """
    if "payer_allocations" in mark or "payer_allocations" not in transfer:
        return same_transfer(transfer, mark) and transfer.get("plan_id") == mark.get(
            "plan_id"
        )
    return (
        same_pair(transfer, mark)
        and transfer["amount"] == mark["amount"]
        and _transfer_dates(transfer) == sorted(mark["dates"])
    )


def mark_is_valid(snapshot, mark, *, ledger=None):
    """A mark stands or falls with its own transfer, not with the whole ledger."""
    if mark.get("revoked_at") or mark.get("confirmed_payment_id"):
        return False
    try:
        scope = settlement_scope(snapshot, mark["dates"], ledger=ledger)
    except ValueError:
        return False
    return any(mark_matches(mark, transfer) for transfer in scope["transfers"])


def annotate_transfers(snapshot, ledger):
    """Mark staleness, and per day every transfer touching it with its status."""
    members = ledger["members"]
    ledger["payer_marks"] = [
        {**m, "stale": not mark_is_valid(snapshot, m, ledger=ledger)}
        for m in ledger["payer_marks"]
    ]
    rows = [
        {"status": "settled", "payment_id": p["id"]}
        | _transfer_row(p, sorted({d for d, _, _ in _deductions(members, p)}))
        for p in ledger["payments"]
        if not p.get("revoked_at")
    ]
    marked = [m for m in ledger["payer_marks"] if not m["stale"]]
    rows += [
        {"status": "marked", "mark_id": m["id"]} | _transfer_row(m, _transfer_dates(m))
        for m in marked
    ]
    try:
        open_transfers = settlement_scope(snapshot, ledger=ledger)["transfers"]
    except ValueError:
        open_transfers = []
    for transfer in open_transfers:
        dates = _transfer_dates(transfer)
        if not any(mark_matches(m, transfer) for m in marked):
            rows.append({"status": "pending"} | _transfer_row(transfer, dates))
    for day in ledger["days"]:
        day["transfers"] = [row for row in rows if day["date"] in row["dates"]]
    return ledger


def same_pair(left, right):
    return all(
        left[key] == right[key]
        for key in ("currency", "from_participant_id", "to_participant_id")
    )


def _transfer_row(transfer, dates):
    return {
        # Handwritten legacy records may lack the endpoints and the amount.
        "currency": transfer["currency"],
        "amount": transfer.get("amount"),
        "from_participant_id": transfer.get("from_participant_id"),
        "to_participant_id": transfer.get("to_participant_id"),
        "dates": dates,
        **({"offset_only": True} if transfer.get("offset_only") else {}),
    }


def member_settlement(snapshot, transfer):
    """Freeze basis and dependencies of a multi-member transfer being confirmed.

    A party is frozen on each day this transfer brings their balance to zero.
    Adjustments that had rolled onto such a day are settled with it, so their
    source days are re-based to today's raw net as well.
    """
    members = sorted(snapshot["members"])
    currency = transfer["currency"]
    by_date = {d["date"]: d for d in project(snapshot)["days"]}
    raw = _raw_days(snapshot)
    frozen = set()
    for member, key in (
        (transfer["to_participant_id"], "allocations"),
        (transfer["from_participant_id"], "payer_allocations"),
    ):
        for allocation in transfer[key]:
            day = by_date[allocation["date"]]
            balance = _day_net(day, currency).get(member, Decimal(0))
            if balance != Decimal(allocation["amount"]):
                continue
            frozen.add((allocation["date"], member))
            frozen |= {
                (carry["source_date"], member)
                for carry in day["carry_entries"]
                if carry["currency"] == currency and carry["participant_id"] == member
            }
    basis = [
        {
            "date": day,
            "currency": currency,
            "participant_id": member,
            "amount": amount(
                currency,
                _balances(raw.get(day, {"groups": []}))
                .get(currency, {})
                .get(member, Decimal(0)),
            ),
        }
        for day, member in sorted(frozen)
    ]
    touched = _member_dates(members, transfer | VERSIONED | {"freeze_basis": basis})
    depends_on = [
        payment["id"]
        for payment in snapshot["state"]["payments"]
        if not payment.get("revoked_at")
        and payment["currency"] == currency
        and any(
            dates & _member_dates(members, payment).get(member, set())
            for member, dates in touched.items()
        )
    ]
    return {"freeze_basis": basis, "depends_on": depends_on}


def settlement_basis(snapshot, preview, currency):
    ledger = project(snapshot)
    selected = set(preview["dates"])
    sources = selected | {
        c["source_date"]
        for d in ledger["days"]
        if d["date"] in selected
        for c in d["carry_entries"]
        if c["currency"] == currency
    }
    raw = _raw_days(snapshot)
    return [
        {
            "date": d,
            "currency": currency,
            "amount": amount(
                currency,
                _balances(raw.get(d, {"groups": []}))
                .get(currency, {})
                .get(min(snapshot["members"]), Decimal(0)),
            ),
        }
        for d in sorted(sources)
    ]


def basis_currencies(snapshot, basis):
    raw = _raw_days(snapshot)
    return {
        b["date"]: sorted(_balances(raw.get(b["date"], {"groups": []}))) for b in basis
    }
