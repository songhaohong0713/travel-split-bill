"""Explicit historical confirmation, never inferred from a published calculation."""

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.core.expense_validation import _check_money
from app.core.ledger import (
    amount,
    basis_currencies,
    payment_preview,
    project,
    settlement_basis,
    snapshot_token,
)


def historical_plan(old, selection, user_id):
    if old["state"].get("history_confirmation", {}).get("status") != "pending":
        raise ValueError("当前旅行没有待确认的历史结算")
    dates = sorted({record["occurred_at"] for record in old["expenses"]})
    if not dates:
        raise ValueError("没有历史消费需要确认")
    stage = deepcopy(old)
    stage["state"]["closed_dates"] = sorted(
        set(stage["state"]["closed_dates"]) | set(dates)
    )
    stage["state"]["history_confirmation"] = {
        "status": "confirmed",
        "confirmed_by": user_id,
        "confirmed_at": datetime.now(UTC).isoformat(),
        "mode": selection["mode"],
        "selection": deepcopy(selection),
        "basis_revisions": [
            {"id": r["id"], "revision": r["revision"]} for r in old["expenses"]
        ],
        "basis_members": list(old["members"]),
        "basis_currency": old["currency"],
        "basis_revision": old["revision"],
    }
    added = []
    mode = selection["mode"]
    if mode != "partial" and selection.get("payments"):
        raise ValueError("只有部分转账模式需要填写实际转账")
    if mode == "settled":
        proposals = payment_preview(stage, dates)["groups"]
    elif mode == "partial":
        proposals = selection.get("payments") or []
        if not proposals:
            raise ValueError("请填写历史已转金额")
    elif mode == "unpaid":
        payment_preview(stage, dates)  # Refuse incomplete historic expenses too.
        proposals = []
    else:
        raise ValueError("不支持的历史确认模式")
    for proposal in proposals:
        group = proposal
        if mode == "partial":
            day = proposal["date"]
            if day not in dates:
                raise ValueError("历史转账日期不属于已有消费")
            groups = payment_preview(stage, [day])["groups"]
            group = next(
                (g for g in groups if g["currency"] == proposal["currency"]), None
            )
            if not group or group["offset_only"]:
                raise ValueError("所选日期和币种没有待转金额")
            _check_money(proposal["currency"], proposal["amount"], positive=True)
            if Decimal(proposal["amount"]) > Decimal(group["amount"]):
                raise ValueError("历史已付金额超过该日待付金额，请核对原账单")
            if any(
                proposal[key] != group[key]
                for key in ["from_participant_id", "to_participant_id"]
            ):
                raise ValueError("历史转账方向与账单不一致")
            signed = Decimal(proposal["amount"]) * (
                1 if group["to_participant_id"] == min(old["members"]) else -1
            )
            group = {
                **group,
                "amount": proposal["amount"],
                "allocations": [
                    {"date": day, "amount": amount(group["currency"], signed)}
                ],
            }
        payment = {
            **group,
            "id": str(uuid4()),
            "created_by": user_id,
            "created_at": datetime.now(UTC).isoformat(),
            "source": "historical_confirmation",
            "paid_on": proposal.get("paid_on") or selection.get("paid_on"),
        }
        scope = {"dates": [a["date"] for a in group["allocations"]]}
        remaining = payment_preview(stage, scope["dates"])["groups"]
        outstanding = next(
            (g for g in remaining if g["currency"] == group["currency"]), None
        )
        basis = (
            settlement_basis(stage, scope, group["currency"])
            if outstanding
            and Decimal(outstanding["amount"]) == Decimal(group["amount"])
            else []
        )
        payment["freeze_basis"] = basis
        payment["basis_currencies"] = basis_currencies(stage, basis)
        basis_dates = {b["date"] for b in basis}
        payment["depends_on"] = [
            p["id"]
            for p in stage["state"]["payments"]
            if not p.get("revoked_at")
            and p["currency"] == group["currency"]
            and any(
                a["date"] in basis_dates
                for a in p["allocations"] + p.get("freeze_basis", [])
            )
        ]
        stage["state"]["payments"].append(payment)
        added.append(payment)
    return {
        "token": snapshot_token({"snapshot": old, "selection": selection}),
        "dates": dates,
        "payments": added,
        "remaining": project(stage),
        "state": stage["state"],
    }
