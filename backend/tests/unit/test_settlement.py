from decimal import Decimal

import pytest
from app.core.allocation import (
    Allocation,
    allocate_decimal,
    equal_allocation,
    person_allocation,
)
from app.core.money import Money, quantize
from app.core.settlement import (
    Adjustment,
    CalculateSettlementInput,
    Expense,
    LineItem,
    Transfer,
    calculate_settlement,
    later_tax_refund,
)
from hypothesis import given
from hypothesis import strategies as st


def money(currency: str, amount: str) -> Money:
    return Money(currency=currency, amount=amount)


@pytest.mark.parametrize(
    ("currency", "amount", "expected"),
    [
        ("JPY", "123.5", "124"),
        ("CNY", "12.345", "12.35"),
        ("KRW", "999.4", "999"),
    ],
)
def test_currency_quantization(currency: str, amount: str, expected: str) -> None:
    assert quantize(money(currency, amount)).amount == expected


def test_actual_cny_payment_overrides_reference_rate_with_discounts() -> None:
    expense = Expense(
        expense_id="tokyo-purchase",
        payer_id="owner",
        items=(
            LineItem(
                item_id="owner-camera",
                amount=money("JPY", "6000"),
                allocation=person_allocation("owner"),
            ),
            LineItem(
                item_id="friend-gift",
                amount=money("JPY", "4000"),
                allocation=person_allocation("friend"),
            ),
        ),
        adjustments=(
            Adjustment(
                adjustment_id="owner-coupon",
                amount=money("JPY", "-500"),
                allocation=person_allocation("owner"),
                reason="personal_coupon",
            ),
            Adjustment(
                adjustment_id="store-discount",
                amount=money("JPY", "-500"),
                allocation=Allocation.from_shares({"owner": "0.6", "friend": "0.4"}),
                reason="shared_discount",
            ),
        ),
        actual_payment=money("CNY", "398.89"),
        reference_rate="0.04000000",
        reference_rate_source="reference-rate-that-must-not-be-used",
    )

    result = calculate_settlement(
        CalculateSettlementInput(
            settlement_currency="CNY",
            participants=("owner", "friend"),
            expenses=(expense,),
        )
    )

    assert result.responsibility_by_participant["friend"].amount == "168.42"
    assert result.transfers == (Transfer("friend", "owner", money("CNY", "168.42")),)
    assert {line.rate_source for line in result.audit_lines} == {"actual-payment"}
    assert any(line.reason == "personal_coupon" for line in result.audit_lines)
    assert any(line.reason == "shared_discount" for line in result.audit_lines)


def test_actual_payment_can_be_converted_to_a_different_settlement_currency() -> None:
    expense = Expense(
        expense_id="seoul-purchase",
        payer_id="owner",
        items=(
            LineItem(
                item_id="friend-item",
                amount=money("KRW", "10000"),
                allocation=person_allocation("friend"),
            ),
        ),
        actual_payment=money("CNY", "50"),
        payment_to_settlement_rate="0.14",
        payment_to_settlement_rate_source="manual-payment-rate",
    )

    result = calculate_settlement(
        CalculateSettlementInput(
            settlement_currency="USD",
            participants=("owner", "friend"),
            expenses=(expense,),
        )
    )

    assert result.responsibility_by_participant["friend"] == money("USD", "7.00")
    assert result.paid_by_participant["owner"] == money("USD", "7.00")
    assert {line.rate_source for line in result.audit_lines} == {
        "actual-payment+manual-payment-rate"
    }


def test_tax_included_in_item_price_is_not_charged_twice() -> None:
    expense = Expense(
        expense_id="tax-inclusive",
        payer_id="owner",
        items=(
            LineItem(
                item_id="friend-item",
                amount=money("JPY", "1100"),
                tax_amount=money("JPY", "100"),
                tax_included=True,
                allocation=person_allocation("friend"),
            ),
        ),
        actual_payment=money("JPY", "1100"),
    )

    result = calculate_settlement(
        CalculateSettlementInput(
            settlement_currency="JPY",
            participants=("owner", "friend"),
            expenses=(expense,),
        )
    )

    assert result.responsibility_by_participant["friend"] == money("JPY", "1100")


def test_later_tax_refund_is_allocated_by_item_tax_not_total_price() -> None:
    items = (
        LineItem(
            item_id="owner-item",
            amount=money("CNY", "100"),
            tax_amount=money("CNY", "10"),
            allocation=person_allocation("owner"),
        ),
        LineItem(
            item_id="friend-item",
            amount=money("CNY", "100"),
            tax_amount=money("CNY", "90"),
            allocation=person_allocation("friend"),
        ),
    )
    refund = later_tax_refund(
        adjustment_id="airport-tax-refund",
        amount=money("CNY", "-50"),
        received_by="owner",
        items=items,
    )
    expense = Expense(
        expense_id="tax-refund-expense",
        payer_id="owner",
        items=items,
        adjustments=(refund,),
        actual_payment=money("CNY", "300"),
    )

    result = calculate_settlement(
        CalculateSettlementInput(
            settlement_currency="CNY",
            participants=("owner", "friend"),
            expenses=(expense,),
        )
    )

    refund_lines = [
        line for line in result.audit_lines if line.reason == "later_tax_refund"
    ]
    assert {line.participant_id: line.amount.amount for line in refund_lines} == {
        "owner": "-5",
        "friend": "-45",
    }
    assert result.responsibility_by_participant["friend"].amount == "145.00"
    assert result.paid_by_participant["owner"].amount == "250.00"


def test_refund_is_a_negative_adjustment() -> None:
    expense = Expense(
        expense_id="partial-refund",
        payer_id="owner",
        items=(
            LineItem(
                item_id="shared-purchase",
                amount=money("CNY", "100"),
                allocation=equal_allocation(("owner", "friend")),
            ),
        ),
        adjustments=(
            Adjustment(
                adjustment_id="friend-return",
                amount=money("CNY", "-20"),
                allocation=person_allocation("friend"),
                reason="refund",
            ),
        ),
        actual_payment=money("CNY", "80"),
    )

    result = calculate_settlement(
        CalculateSettlementInput(
            settlement_currency="CNY",
            participants=("owner", "friend"),
            expenses=(expense,),
        )
    )

    assert result.responsibility_by_participant["friend"].amount == "30.00"
    refund_line = next(line for line in result.audit_lines if line.reason == "refund")
    assert refund_line.amount.amount == "-20"


def test_three_person_netting_is_deterministic_and_uses_two_transfers() -> None:
    expense = Expense(
        expense_id="shared-dinner",
        payer_id="alice",
        items=(
            LineItem(
                item_id="dinner",
                amount=money("CNY", "90"),
                allocation=equal_allocation(("alice", "bob", "cara")),
            ),
        ),
        actual_payment=money("CNY", "90"),
    )

    result = calculate_settlement(
        CalculateSettlementInput(
            settlement_currency="CNY",
            participants=("cara", "alice", "bob"),
            expenses=(expense,),
        )
    )

    assert result.transfers == (
        Transfer("bob", "alice", money("CNY", "30.00")),
        Transfer("cara", "alice", money("CNY", "30.00")),
    )


@given(st.lists(st.integers(min_value=1, max_value=10_000), min_size=1, max_size=8))
def test_generated_allocation_shares_total_exactly_one(weights: list[int]) -> None:
    allocation = Allocation.from_weights(
        {f"p{index}": str(weight) for index, weight in enumerate(weights)}
    )

    assert sum(allocation.as_dict().values(), start=Decimal(0)) == Decimal(1)


@given(
    amount=st.integers(min_value=-1_000_000, max_value=1_000_000),
    weights=st.lists(
        st.integers(min_value=1, max_value=10_000), min_size=1, max_size=8
    ),
)
def test_generated_allocations_conserve_responsibility(
    amount: int, weights: list[int]
) -> None:
    allocation = Allocation.from_weights(
        {f"p{index}": str(weight) for index, weight in enumerate(weights)}
    )

    allocated = allocate_decimal(Decimal(amount), allocation)

    assert sum(allocated.values(), start=Decimal(0)) == Decimal(amount)


@given(
    amount=st.integers(min_value=1, max_value=1_000_000),
    weights=st.lists(
        st.integers(min_value=1, max_value=10_000), min_size=1, max_size=8
    ),
)
def test_generated_quantized_net_sums_to_zero(amount: int, weights: list[int]) -> None:
    participants = tuple(f"p{index}" for index in range(len(weights)))
    expense = Expense(
        expense_id="generated",
        payer_id=participants[0],
        items=(
            LineItem(
                item_id="generated-item",
                amount=money("JPY", str(amount)),
                allocation=Allocation.from_weights(
                    {
                        participant: str(weight)
                        for participant, weight in zip(
                            participants, weights, strict=True
                        )
                    }
                ),
            ),
        ),
        actual_payment=money("JPY", str(amount)),
    )

    result = calculate_settlement(
        CalculateSettlementInput(
            settlement_currency="JPY",
            participants=participants,
            expenses=(expense,),
        )
    )

    assert sum(
        (balance.decimal for balance in result.net_by_participant.values()),
        start=Decimal(0),
    ) == Decimal(0)
