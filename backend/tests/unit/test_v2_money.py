from dataclasses import replace

from app.core.allocation import equal_allocation
from app.core.money import Money
from app.core.settlement import (
    CalculateSettlementInput,
    Expense,
    LineItem,
    calculate_settlement,
)


def test_v2_source_remainder_before_conversion_and_legacy_unchanged():
    expense = Expense(
        expense_id="e",
        payer_id="a",
        items=(LineItem("i", Money("JPY", "1281"), equal_allocation(("a", "b"))),),
        reference_rate="0.04236",
    )

    def responsibility(value):
        return calculate_settlement(
            CalculateSettlementInput("CNY", ("a", "b"), (value,))
        ).responsibility_by_participant

    assert responsibility(expense)["b"].amount == "27.13"
    v2 = responsibility(replace(expense, calculation_version="v2"))
    assert v2["b"].amount == "27.11"
    assert v2["a"].amount == "27.15"


def test_v2_exact_custom_source_amounts_override_repeating_shares():
    expense = Expense(
        expense_id="e",
        payer_id="a",
        calculation_version="v2",
        items=(
            LineItem(
                "i",
                Money("JPY", "3"),
                equal_allocation(("a", "b")),
                allocation_amounts={"a": "1", "b": "2"},
            ),
        ),
        reference_rate="1",
    )
    result = calculate_settlement(
        CalculateSettlementInput("CNY", ("a", "b"), (expense,))
    )
    assert result.responsibility_by_participant["b"].amount == "2.00"
