"""Daily balances reuse the money engine; payments offset, never rewrite, expenses."""

import hashlib
import json
from decimal import Decimal

from app.api.v1.settlements import ExpenseInput, _grouped_payload
from app.core.money import quantized_money


def snapshot_token(snapshot):
    return hashlib.sha256(
        json.dumps(
            snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def amount(currency, value):
    return quantized_money(currency, value).amount


def project(snapshot):
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
