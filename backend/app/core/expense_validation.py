"""Validate persisted expenses using the same rules as the settlement calculator."""

import json
from decimal import Decimal

from app.core.money import currency_scale, decimal_from_string, quantize_decimal
from app.core.settlement import calculate_grouped_settlement


def validate_expense_payload(
    payload: dict, members: set[str], default_currency: str
) -> None:
    # Lazy import preserves the existing public schema imports without a router cycle.
    from app.api.v1.settlements import ExpenseInput

    if len(json.dumps(payload, ensure_ascii=False).encode()) > 500_000:
        raise ValueError("消费明细过大")
    currency_scale(default_currency)
    people = payload.get("participants")
    if (
        not isinstance(people, list)
        or not people
        or len(people) > 2
        or any(not isinstance(p, str) or not p for p in people)
    ):
        raise ValueError("请提供有效的参与成员")
    if len(set(people)) != len(people) or not set(people).issubset(members):
        raise ValueError("参与成员必须属于当前旅行且不能重复")
    if len(str(payload.get("title", ""))) > 120:
        raise ValueError("消费名称最多120字")
    currency = payload.get("settlement_currency") or default_currency
    currency_scale(currency)
    raw = payload.get("expenses")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 100:
        raise ValueError("消费缺少有效明细")
    expenses = []
    expense_ids = set()
    for value in raw:
        parsed = ExpenseInput.model_validate(value)
        if (
            not parsed.expense_id
            or len(parsed.expense_id) > 120
            or parsed.expense_id in expense_ids
        ):
            raise ValueError("消费标识为空或重复")
        expense_ids.add(parsed.expense_id)
        if parsed.payer_id not in people or not 1 <= len(parsed.items) <= 1000:
            raise ValueError("付款人或商品明细无效")
        item_ids = set()
        for item in parsed.items:
            if not item.item_id or len(item.item_id) > 120 or item.item_id in item_ids:
                raise ValueError("商品标识为空或重复")
            item_ids.add(item.item_id)
            _check_money(item.amount.currency, item.amount.amount, positive=True)
            if item.tax_amount:
                _check_money(
                    item.tax_amount.currency, item.tax_amount.amount, nonnegative=True
                )
        if len(parsed.adjustments) > 100:
            raise ValueError("账单调整过多")
        ids = [a.adjustment_id for a in parsed.adjustments]
        if len(ids) != len(set(ids)) or any(not i or len(i) > 120 for i in ids):
            raise ValueError("调整标识为空或重复")
        for adjustment in parsed.adjustments:
            _check_money(adjustment.amount.currency, adjustment.amount.amount)
        if parsed.actual_payment:
            _check_money(
                parsed.actual_payment.currency,
                parsed.actual_payment.amount,
                positive=True,
            )
        for rate in [parsed.reference_rate, parsed.payment_to_settlement_rate]:
            if rate is not None and (
                len(rate) > 30
                or not Decimal(0) < decimal_from_string(rate) <= Decimal(1000000000)
            ):
                raise ValueError("汇率必须是有效正数")
        expenses.append(parsed.to_expense(currency))
    calculate_grouped_settlement(tuple(people), tuple(expenses))


def _check_money(currency: str, text: str, positive=False, nonnegative=False) -> None:
    currency_scale(currency)
    if len(text) > 24:
        raise ValueError("金额超出允许范围")
    value = decimal_from_string(text)
    if abs(value) > Decimal(1000000000000) or value != quantize_decimal(
        value, currency
    ):
        raise ValueError("金额精度或范围不正确")
    if (positive and value <= 0) or (nonnegative and value < 0):
        raise ValueError("商品和实际支付金额必须大于零，税额不能为负")
