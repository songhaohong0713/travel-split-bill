"""Daily balances reuse the money engine; payments offset, never rewrite, expenses."""

import hashlib
import json
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal

from app.api.v1.settlements import ExpenseInput, _grouped_payload
from app.core.business_time import (
    business_today,
    client_timezone_offset,  # noqa: F401 - compatibility re-export
)
from app.core.money import quantized_money


def snapshot_token(snapshot):
    return hashlib.sha256(
        json.dumps(
            snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def amount(currency, value):
    return quantized_money(currency, value).amount


def _raw_project(snapshot):
    members = sorted(snapshot["members"])
    state = snapshot["state"]
    dates = set(state["closed_dates"]) | {
        r["occurred_at"] for r in snapshot["expenses"]
    }
    dates |= {a["date"] for p in state["payments"] for a in p["allocations"]}
    first = members[0]
    days = []
    for day in sorted(dates, reverse=True):
        records = [r for r in snapshot["expenses"] if r["occurred_at"] == day]
        balances = {}
        groups = []
        error = ""
        try:
            expenses = []
            for record in records:
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
            for group in groups:
                currency = group["currency"]
                balances[currency] = Decimal(
                    group["net_by_participant"][first]["amount"]
                )
        except (ValueError, ArithmeticError, TypeError, AttributeError):
            error = "消费分摊或汇率不完整，请打开消费修正"
        for payment in state["payments"]:
            if payment.get("revoked_at"):
                continue
            for allocation in payment["allocations"]:
                if allocation["date"] == day:
                    currency = payment["currency"]
                    balances[currency] = balances.get(currency, Decimal(0)) - Decimal(
                        allocation["amount"]
                    )
        pending = []
        for currency, balance in sorted(balances.items()):
            if balance:
                if len(members) != 2:
                    error = "请等待同行人加入后结清"
                    continue
                pending.append(
                    {
                        "currency": currency,
                        "amount": amount(currency, abs(balance)),
                        "signed_amount": amount(currency, balance),
                        "from_participant_id": members[1] if balance > 0 else first,
                        "to_participant_id": first if balance > 0 else members[1],
                    }
                )
        status = (
            "recording"
            if day not in state["closed_dates"]
            else "invalid"
            if error
            else "pending"
            if pending
            else "settled"
        )
        days.append(
            {
                "date": day,
                "status": status,
                "error": error,
                "count": len(records),
                "expense_ids": [r["id"] for r in records],
                "groups": groups,
                "pending": pending,
            }
        )
    return {
        "days": days,
        "payments": state["payments"],
        "revision": snapshot["revision"],
        "members": members,
        "history_status": state.get("history_confirmation", {}).get(
            "status", "not_required"
        ),
    }


def payment_preview(snapshot, requested_dates=None):
    ledger = project(snapshot)
    selected = (
        sorted(set(requested_dates))
        if requested_dates is not None
        else sorted(d["date"] for d in ledger["days"] if d["status"] != "recording")
    )
    if not selected:
        raise ValueError("请先手动结束当天记账")
    by_date = {d["date"]: d for d in ledger["days"]}
    if any(day >= business_today() for day in selected):
        raise ValueError("今天仍在记录中，不能结清")
    if any(
        day not in by_date or by_date[day]["status"] in ("recording", "invalid")
        for day in selected
    ):
        raise ValueError("所选日期仍在记录中或消费数据不完整")
    currencies = sorted(
        {p["currency"] for day in selected for p in by_date[day]["pending"]}
    )
    groups = []
    members = ledger["members"]
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
    return {"dates": selected, "groups": groups, "token": snapshot_token(snapshot)}


def _raw_days(snapshot, records=None):
    return {
        d["date"]: d
        for d in _raw_project(
            {
                **snapshot,
                "expenses": snapshot["expenses"] if records is None else records,
                "state": {"closed_dates": [], "payments": []},
            }
        )["days"]
    }


def _balances(day):
    return {
        g["currency"]: Decimal(
            g["net_by_participant"][min(g["net_by_participant"])]["amount"]
        )
        for g in day["groups"]
    }


def project(snapshot):
    raw = _raw_days(snapshot)
    active = [p for p in snapshot["state"]["payments"] if not p.get("revoked_at")]
    frozen = {}
    known_currencies = {}
    active_ids = {p["id"] for p in active}
    for payment in active:
        if not set(payment.get("depends_on", [])).issubset(active_ids):
            continue
        evidence = _raw_days(snapshot, payment.get("basis_expenses", []))
        evidence_dates = {a["date"] for a in payment["allocations"]} | {
            b["date"] for b in payment.get("freeze_basis", [])
        }
        for day in evidence_dates:
            known_currencies[day] = set(_balances(evidence.get(day, {"groups": []})))
        if "freeze_basis" in payment:
            for basis in payment["freeze_basis"]:
                frozen[basis["date"], basis["currency"]] = Decimal(basis["amount"])
        else:
            basis = _raw_days(snapshot, payment.get("basis_expenses", []))
            for allocation in payment["allocations"]:
                key = allocation["date"], payment["currency"]
                original = _balances(basis.get(key[0], {"groups": []})).get(key[1])
                if original is not None and original == Decimal(allocation["amount"]):
                    frozen[key] = original
    for day, currencies in known_currencies.items():
        if currencies and all((day, c) in frozen for c in currencies):
            for currency in _balances(raw.get(day, {"groups": []})):
                frozen.setdefault((day, currency), Decimal(0))
    dates = set(raw) | set(snapshot["state"]["closed_dates"])
    dates |= {
        a["date"] for p in snapshot["state"]["payments"] for a in p["allocations"]
    }
    balances = {d: _balances(raw.get(d, {"groups": []})) for d in dates}
    carries = {d: [] for d in dates}
    for payment in active:
        for allocation in payment["allocations"]:
            day, currency = allocation["date"], payment["currency"]
            if (day, currency) not in frozen:
                balances[day][currency] = balances[day].get(
                    currency, Decimal(0)
                ) - Decimal(allocation["amount"])
    today = business_today()
    for day, currency in frozen:
        balances.setdefault(day, {})[currency] = Decimal(0)
    for (day, currency), original in sorted(frozen.items()):
        current = _balances(raw.get(day, {"groups": []})).get(currency, Decimal(0))
        delta = current - original
        if not delta:
            continue
        target = next(
            (d for d in sorted(dates) if d > day and (d, currency) not in frozen),
            (date.fromisoformat(day) + timedelta(days=1)).isoformat(),
        )
        while (target, currency) in frozen:
            target = (date.fromisoformat(target) + timedelta(days=1)).isoformat()
        dates.add(target)
        balances.setdefault(target, {})[currency] = (
            balances.get(target, {}).get(currency, Decimal(0)) + delta
        )
        carries.setdefault(target, []).append(
            {
                "kind": "adjustment",
                "source_date": day,
                "currency": currency,
                "signed_amount": amount(currency, delta),
                "amount": amount(currency, abs(delta)),
            }
        )
    days = []
    members = sorted(snapshot["members"])
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
                "signed_amount": amount(g["currency"], _balances(row)[g["currency"]]),
            }
            for g in row["groups"]
        ]
        row["carry_entries"] = carries.get(day, [])
        row["pending"] = []
        for currency, value in sorted(balances[day].items()):
            if value and len(members) == 2:
                row["pending"].append(
                    {
                        "currency": currency,
                        "amount": amount(currency, abs(value)),
                        "signed_amount": amount(currency, value),
                        "from_participant_id": members[1] if value > 0 else members[0],
                        "to_participant_id": members[0] if value > 0 else members[1],
                    }
                )
            elif value:
                row["error"] = "请等待同行人加入后结清"
        recording = day >= today
        row["status"] = (
            "invalid"
            if row["error"]
            else "recording"
            if recording
            else "pending"
            if row["pending"]
            else "settled"
        )
        row["settled_transfers"] = [
            p for p in active if any(a["date"] == day for a in p["allocations"])
        ]
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
                _balances(raw.get(d, {"groups": []})).get(currency, Decimal(0)),
            ),
        }
        for d in sorted(sources)
    ]
