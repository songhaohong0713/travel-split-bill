from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.allocation import Allocation, allocate_decimal
from app.core.money import (
    Money,
    decimal_from_string,
    decimal_to_string,
    quantize_decimal,
    quantized_money,
)


@dataclass(frozen=True)
class LineItem:
    item_id: str
    amount: Money
    allocation: Allocation
    tax_amount: Money | None = None


@dataclass(frozen=True)
class Adjustment:
    adjustment_id: str
    amount: Money
    allocation: Allocation
    reason: str
    received_by: str | None = None


@dataclass(frozen=True)
class Expense:
    expense_id: str
    payer_id: str
    items: tuple[LineItem, ...]
    adjustments: tuple[Adjustment, ...] = field(default_factory=tuple)
    actual_payment: Money | None = None
    reference_rate: str | None = None
    reference_rate_source: str | None = None
    rounding_owner_id: str | None = None


@dataclass(frozen=True)
class CalculateSettlementInput:
    settlement_currency: str
    participants: tuple[str, ...]
    expenses: tuple[Expense, ...]


@dataclass(frozen=True)
class AuditLine:
    expense_id: str
    participant_id: str
    amount: Money
    reason: str
    rate_source: str


@dataclass(frozen=True)
class Transfer:
    from_participant_id: str
    to_participant_id: str
    amount: Money


@dataclass(frozen=True)
class SettlementResult:
    responsibility_by_participant: dict[str, Money]
    paid_by_participant: dict[str, Money]
    net_by_participant: dict[str, Money]
    transfers: tuple[Transfer, ...]
    audit_lines: tuple[AuditLine, ...]


def later_tax_refund(
    *,
    adjustment_id: str,
    amount: Money,
    received_by: str,
    items: Sequence[LineItem],
) -> Adjustment:
    if amount.decimal >= 0:
        raise ValueError("a later tax refund must be a negative amount")
    tax_by_participant: dict[str, Decimal] = {}
    for item in sorted(items, key=lambda value: value.item_id):
        if item.tax_amount is None:
            raise ValueError("every item needs tax_amount for a tax-based refund")
        if item.tax_amount.currency != amount.currency:
            raise ValueError("refund and item tax currencies must match")
        for participant_id, tax in allocate_decimal(
            item.tax_amount.decimal, item.allocation
        ).items():
            tax_by_participant[participant_id] = (
                tax_by_participant.get(participant_id, Decimal(0)) + tax
            )
    positive_tax = {
        participant_id: tax
        for participant_id, tax in tax_by_participant.items()
        if tax > 0
    }
    if not positive_tax:
        raise ValueError("tax-based refund requires positive allocated item tax")
    allocation = Allocation.from_weights(
        {
            participant_id: decimal_to_string(tax)
            for participant_id, tax in positive_tax.items()
        }
    )
    return Adjustment(
        adjustment_id=adjustment_id,
        amount=amount,
        allocation=allocation,
        reason="later_tax_refund",
        received_by=received_by,
    )


def calculate_settlement(data: CalculateSettlementInput) -> SettlementResult:
    participants = _validated_participants(data.participants)
    settlement_currency = data.settlement_currency
    quantize_decimal(Decimal(0), settlement_currency)

    responsibility = {participant_id: Decimal(0) for participant_id in participants}
    paid = {participant_id: Decimal(0) for participant_id in participants}
    audit_lines: list[AuditLine] = []

    for expense in sorted(data.expenses, key=lambda value: value.expense_id):
        expense_responsibility, expense_paid, expense_audit = _calculate_expense(
            expense=expense,
            participants=participants,
            settlement_currency=settlement_currency,
        )
        for participant_id in participants:
            responsibility[participant_id] += expense_responsibility[participant_id]
            paid[participant_id] += expense_paid[participant_id]
        audit_lines.extend(expense_audit)

    net = {
        participant_id: paid[participant_id] - responsibility[participant_id]
        for participant_id in participants
    }
    if sum(net.values(), start=Decimal(0)) != Decimal(0):
        raise AssertionError("quantized net balances must sum to zero")

    return SettlementResult(
        responsibility_by_participant=_money_map(
            responsibility, settlement_currency
        ),
        paid_by_participant=_money_map(paid, settlement_currency),
        net_by_participant=_money_map(net, settlement_currency),
        transfers=_minimum_transfers(net, settlement_currency),
        audit_lines=tuple(audit_lines),
    )


def _calculate_expense(
    *,
    expense: Expense,
    participants: tuple[str, ...],
    settlement_currency: str,
) -> tuple[dict[str, Decimal], dict[str, Decimal], list[AuditLine]]:
    if expense.payer_id not in participants:
        raise ValueError(f"unknown payer: {expense.payer_id}")
    if not expense.items:
        raise ValueError("expense must include at least one item")

    source_currency = expense.items[0].amount.currency
    components: list[tuple[str, Decimal, str]] = []
    base_total = Decimal(0)
    cash_adjustments: list[Adjustment] = []

    for item in sorted(expense.items, key=lambda value: value.item_id):
        _require_currency(item.amount, source_currency)
        _validate_allocation(item.allocation, participants)
        base_total += item.amount.decimal
        _append_allocated_component(
            components, item.amount.decimal, item.allocation, f"item:{item.item_id}"
        )
        if item.tax_amount is not None:
            _require_currency(item.tax_amount, source_currency)
            base_total += item.tax_amount.decimal
            _append_allocated_component(
                components,
                item.tax_amount.decimal,
                item.allocation,
                f"item_tax:{item.item_id}",
            )

    for adjustment in sorted(
        expense.adjustments, key=lambda value: value.adjustment_id
    ):
        _require_currency(adjustment.amount, source_currency)
        _validate_allocation(adjustment.allocation, participants)
        _append_allocated_component(
            components,
            adjustment.amount.decimal,
            adjustment.allocation,
            adjustment.reason,
        )
        if adjustment.received_by is None:
            base_total += adjustment.amount.decimal
        else:
            if adjustment.received_by not in participants:
                raise ValueError(f"unknown adjustment recipient: {adjustment.received_by}")
            if adjustment.amount.decimal >= 0:
                raise ValueError("received adjustments must be negative refunds")
            cash_adjustments.append(adjustment)

    rate, rate_source, base_paid = _resolve_rate(
        expense=expense,
        source_currency=source_currency,
        settlement_currency=settlement_currency,
        base_total=base_total,
    )

    raw_responsibility = {
        participant_id: Decimal(0) for participant_id in participants
    }
    audit_lines: list[AuditLine] = []
    for participant_id, source_amount, reason in components:
        converted = source_amount * rate
        raw_responsibility[participant_id] += converted
        audit_lines.append(
            AuditLine(
                expense_id=expense.expense_id,
                participant_id=participant_id,
                amount=Money.from_decimal(settlement_currency, converted),
                reason=reason,
                rate_source=rate_source,
            )
        )

    expense_responsibility = {
        participant_id: quantize_decimal(amount, settlement_currency)
        for participant_id, amount in raw_responsibility.items()
    }
    for participant_id in participants:
        quantization_difference = (
            expense_responsibility[participant_id]
            - raw_responsibility[participant_id]
        )
        if quantization_difference != 0:
            audit_lines.append(
                AuditLine(
                    expense_id=expense.expense_id,
                    participant_id=participant_id,
                    amount=Money.from_decimal(
                        settlement_currency, quantization_difference
                    ),
                    reason="quantization",
                    rate_source=rate_source,
                )
            )

    raw_paid = {participant_id: Decimal(0) for participant_id in participants}
    raw_paid[expense.payer_id] += base_paid
    for adjustment in cash_adjustments:
        assert adjustment.received_by is not None
        raw_paid[adjustment.received_by] += adjustment.amount.decimal * rate
    expense_paid = {
        participant_id: quantize_decimal(amount, settlement_currency)
        for participant_id, amount in raw_paid.items()
    }

    rounding_owner = expense.rounding_owner_id or expense.payer_id
    if rounding_owner not in participants:
        raise ValueError(f"unknown rounding owner: {rounding_owner}")
    rounding_difference = sum(
        expense_paid.values(), start=Decimal(0)
    ) - sum(expense_responsibility.values(), start=Decimal(0))
    expense_responsibility[rounding_owner] += rounding_difference
    audit_lines.append(
        AuditLine(
            expense_id=expense.expense_id,
            participant_id=rounding_owner,
            amount=quantized_money(settlement_currency, rounding_difference),
            reason="rounding",
            rate_source=rate_source,
        )
    )
    return expense_responsibility, expense_paid, audit_lines


def _resolve_rate(
    *,
    expense: Expense,
    source_currency: str,
    settlement_currency: str,
    base_total: Decimal,
) -> tuple[Decimal, str, Decimal]:
    if expense.actual_payment is not None:
        if expense.actual_payment.currency != settlement_currency:
            raise ValueError("actual payment must use the settlement currency")
        if base_total == 0:
            raise ValueError("cannot map an actual payment from a zero source total")
        return (
            expense.actual_payment.decimal / base_total,
            "actual-payment",
            expense.actual_payment.decimal,
        )
    if source_currency == settlement_currency:
        return Decimal(1), "same-currency", base_total
    if expense.reference_rate is None:
        raise ValueError("cross-currency expense requires actual payment or a saved rate")
    rate = decimal_from_string(expense.reference_rate, label="reference rate")
    if rate <= 0:
        raise ValueError("reference rate must be positive")
    source = expense.reference_rate_source or "reference-rate"
    return rate, source, base_total * rate


def _append_allocated_component(
    components: list[tuple[str, Decimal, str]],
    amount: Decimal,
    allocation: Allocation,
    reason: str,
) -> None:
    if not reason:
        raise ValueError("audit reason cannot be empty")
    for participant_id, participant_amount in allocate_decimal(
        amount, allocation
    ).items():
        components.append((participant_id, participant_amount, reason))


def _validate_allocation(
    allocation: Allocation, participants: tuple[str, ...]
) -> None:
    unknown = set(allocation.participant_ids) - set(participants)
    if unknown:
        raise ValueError(f"allocation includes unknown participants: {sorted(unknown)}")


def _require_currency(amount: Money, currency: str) -> None:
    if amount.currency != currency:
        raise ValueError("all expense components must use the same source currency")


def _validated_participants(participants: Sequence[str]) -> tuple[str, ...]:
    if not participants:
        raise ValueError("settlement needs at least one participant")
    if any(not participant_id for participant_id in participants):
        raise ValueError("participant id cannot be empty")
    if len(set(participants)) != len(participants):
        raise ValueError("participant ids must be unique")
    return tuple(sorted(participants))


def _money_map(values: dict[str, Decimal], currency: str) -> dict[str, Money]:
    return {
        participant_id: quantized_money(currency, value)
        for participant_id, value in values.items()
    }


def _minimum_transfers(
    net: dict[str, Decimal], currency: str
) -> tuple[Transfer, ...]:
    creditors = sorted(
        ((participant_id, amount) for participant_id, amount in net.items() if amount > 0),
        key=lambda value: (-value[1], value[0]),
    )
    debtors = sorted(
        (
            (participant_id, -amount)
            for participant_id, amount in net.items()
            if amount < 0
        ),
        key=lambda value: (-value[1], value[0]),
    )
    transfers: list[Transfer] = []
    creditor_index = 0
    debtor_index = 0
    while creditor_index < len(creditors) and debtor_index < len(debtors):
        creditor_id, credit = creditors[creditor_index]
        debtor_id, debt = debtors[debtor_index]
        amount = min(credit, debt)
        transfers.append(
            Transfer(
                from_participant_id=debtor_id,
                to_participant_id=creditor_id,
                amount=quantized_money(currency, amount),
            )
        )
        credit -= amount
        debt -= amount
        creditors[creditor_index] = (creditor_id, credit)
        debtors[debtor_index] = (debtor_id, debt)
        if credit == 0:
            creditor_index += 1
        if debt == 0:
            debtor_index += 1
    return tuple(transfers)
