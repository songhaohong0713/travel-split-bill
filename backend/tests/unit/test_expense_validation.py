from copy import deepcopy

import pytest
from app.core.expense_validation import validate_expense_payload


def payload():
    return {
        "participants": ["a", "b"],
        "settlement_currency": "CNY",
        "expenses": [
            {
                "expense_id": "e",
                "payer_id": "a",
                "items": [
                    {
                        "item_id": "i",
                        "name": "茶",
                        "amount": {"currency": "CNY", "amount": "20.00"},
                        "allocation": {"a": "0.5", "b": "0.5"},
                    }
                ],
            }
        ],
    }


def test_valid_payload_and_personal_expense():
    original = payload()
    validate_expense_payload(original, {"a", "b"}, "CNY")
    assert original == payload()
    one = deepcopy(original)
    one["participants"] = ["a"]
    one["expenses"][0]["items"][0]["allocation"] = {"a": "1"}
    validate_expense_payload(one, {"a", "b"}, "CNY")


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "outsider",
        "negative",
        "shares",
        "precision",
        "unsupported",
        "duplicate",
        "rate",
        "zero",
    ],
)
def test_invalid_expenses_are_rejected_before_storage(case):
    value = payload()
    expense = value["expenses"][0]
    item = expense["items"][0]
    if case == "missing":
        value = {}
    if case == "outsider":
        expense["payer_id"] = "outsider"
    if case == "negative":
        item["amount"]["amount"] = "-1"
    if case == "shares":
        item["allocation"] = {"a": "0.8", "b": "0.8"}
    if case == "precision":
        item["amount"]["amount"] = "0.001"
    if case == "unsupported":
        item["amount"]["currency"] = "ZZZ"
    if case == "duplicate":
        expense["items"].append(deepcopy(item))
    if case == "rate":
        item["amount"] = {"currency": "JPY", "amount": "100"}
    if case == "zero":
        item["amount"]["amount"] = "0"
    with pytest.raises(ValueError):
        validate_expense_payload(value, {"a", "b"}, "CNY")
