from copy import deepcopy
from decimal import Decimal

import pytest
from app.core.ledger import _balances, _raw_days, payment_preview, project
from test_daily_ledger import record, snapshot


def test_daily_currency_balances_keep_every_members_decimal_net():
    s = snapshot()
    s["members"] = ["c", "a", "b"]
    bill = s["expenses"][0]["payload"]["expenses"][0]
    bill["payer_id"] = "b"
    bill["items"][0]["allocation"] = {"b": "0.5", "c": "0.5"}

    assert _balances(_raw_days(s)["2020-01-01"]) == {
        "CNY": {"a": Decimal(0), "b": Decimal(200), "c": Decimal(-200)}
    }


def test_pending_includes_transfers_when_first_of_three_members_has_zero_net():
    s = snapshot()
    s["members"] = ["c", "a", "b"]
    bill = s["expenses"][0]["payload"]["expenses"][0]
    bill["payer_id"] = "b"
    bill["items"][0]["allocation"] = {"b": "0.5", "c": "0.5"}

    day = project(s)["days"][0]

    assert day["status"] == "pending"
    assert day["error"] == ""
    assert day["pending"] == [
        {
            "currency": "CNY",
            "amount": "200.00",
            "from_participant_id": "c",
            "to_participant_id": "b",
        }
    ]


def test_pending_matches_shared_transfer_order_for_multiple_creditors_and_currencies():
    s = snapshot()
    s["members"] = ["d", "c", "b", "a"]
    bills = [
        ("one", "CNY", "a", "120", {"c": "1"}),
        ("two", "CNY", "b", "80", {"d": "1"}),
        ("yen", "JPY", "d", "1001", {"a": "0.5", "b": "0.5"}),
    ]
    s["expenses"] = []
    for id, currency, payer, value, allocation in bills:
        r = record(id, "2020-01-01", value, currency)
        bill = r["payload"]["expenses"][0]
        bill["payer_id"] = payer
        bill["items"][0]["allocation"] = allocation
        s["expenses"].append(r)

    day = project(s)["days"][0]

    assert day["error"] == ""
    assert day["pending"] == [
        {
            "currency": "CNY",
            "amount": "120.00",
            "from_participant_id": "c",
            "to_participant_id": "a",
        },
        {
            "currency": "CNY",
            "amount": "80.00",
            "from_participant_id": "d",
            "to_participant_id": "b",
        },
        {
            "currency": "JPY",
            "amount": "501",
            "from_participant_id": "a",
            "to_participant_id": "d",
        },
        {
            "currency": "JPY",
            "amount": "501",
            "from_participant_id": "b",
            "to_participant_id": "d",
        },
    ]


@pytest.mark.parametrize("supplied_ledger", [False, True])
@pytest.mark.parametrize("missing_timezone", [False, True])
def test_preview_rejects_more_than_two_members_before_selecting_transfers(
    supplied_ledger, missing_timezone
):
    s = snapshot()
    s["members"].append("c")
    if missing_timezone:
        s["timezone_offset_minutes"] = None
    kwargs = {"ledger": project(s)} if supplied_ledger else {}

    with pytest.raises(ValueError, match="^多人结清尚未开放$"):
        payment_preview(s, **kwargs)


@pytest.mark.parametrize("signed,expected", [("40.00", "160.00"), ("-40.00", "240.00")])
def test_legacy_allocation_uses_signed_first_member_net_and_offsets_both_members(
    signed, expected
):
    s = snapshot()
    s["members"].reverse()
    s["state"]["payments"].append(
        {
            "id": "partial",
            "currency": "CNY",
            "allocations": [{"date": "2020-01-01", "amount": signed}],
            "from_participant_id": "a",
            "to_participant_id": "b",
        }
    )
    original = deepcopy(s)

    assert project(s)["days"][0]["pending"] == [
        {
            "currency": "CNY",
            "amount": expected,
            "signed_amount": expected,
            "from_participant_id": "b",
            "to_participant_id": "a",
        }
    ]
    assert s == original


def test_single_member_self_expense_has_no_pending_transfer():
    s = snapshot()
    s["members"] = ["a"]
    s["expenses"][0]["payload"]["expenses"][0]["items"][0]["allocation"] = {"a": "1"}

    day = project(s)["days"][0]

    assert day["error"] == ""
    assert day["pending"] == []
    assert day["status"] == "settled"


@pytest.mark.parametrize("new_member", ["0", "b", "z"])
@pytest.mark.parametrize("basis_format", ["basis_expenses", "freeze_basis"])
@pytest.mark.parametrize("payer,signed", [("a", "200.00"), ("c", "-200.00")])
def test_joining_member_does_not_reassign_existing_settlement(
    monkeypatch, new_member, basis_format, payer, signed
):
    from app.core import ledger

    s = snapshot()
    s["members"] = ["a", "c"]
    bill = s["expenses"][0]["payload"]["expenses"][0]
    bill["payer_id"] = payer
    bill["items"][0]["allocation"] = {"a": "0.5", "c": "0.5"}
    payment = {
        "id": "settled-before-join",
        "currency": "CNY",
        "from_participant_id": "c" if payer == "a" else "a",
        "to_participant_id": payer,
        "allocations": [{"date": "2020-01-01", "amount": signed}],
    }
    if basis_format == "basis_expenses":
        payment[basis_format] = deepcopy(s["expenses"])
    else:
        payment[basis_format] = [
            {"date": "2020-01-01", "currency": "CNY", "amount": signed}
        ]
    s["state"]["payments"].append(payment)
    before = project(s)
    assert before["days"][0]["status"] == "settled"

    nets = []
    original_pending = ledger._pending

    def capture_pending(balances, members):
        nets.append(deepcopy(balances))
        return original_pending(balances, members)

    monkeypatch.setattr(ledger, "_pending", capture_pending)
    s["members"].append(new_member)
    after = project(s)

    assert [d["date"] for d in after["days"]] == ["2020-01-01"]
    assert after["days"][0]["status"] == "settled"
    assert after["days"][0]["pending"] == []
    assert after["days"][0]["carry_entries"] == []
    assert nets[-1] == {
        "CNY": {"a": Decimal(0), "c": Decimal(0), new_member: Decimal(0)}
    }
    assert after["payments"] == before["payments"]
