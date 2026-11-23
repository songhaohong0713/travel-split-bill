"""Claiming and removing placeholder members, on snapshots."""

from copy import deepcopy
from decimal import Decimal

import pytest
from app.core.ledger import payment_preview, project, settlement_basis
from app.core.placeholders import ClaimRefused, claim, mentions, removal, renamed
from test_daily_ledger import record, snapshot


def pair_trip(placeholder, real="m"):
    """A two-member trip of one real member and a placeholder, one day settled."""
    s = snapshot()
    s["members"] = sorted([real, placeholder])
    s["expenses"] = [
        record("one", "2020-01-01", "400"),
        record("two", "2020-01-02", "100"),
    ]
    for expense in s["expenses"]:
        bill = expense["payload"]["expenses"][0]
        bill["payer_id"] = real
        bill["items"][0]["allocation"] = {real: "0.5", placeholder: "0.5"}
        expense["payload"]["participants"] = [real, placeholder]
    p = payment_preview(s, ["2020-01-01"])
    group = p["groups"][0]
    s["state"]["payments"].append(
        {
            **group,
            "id": "paid",
            "created_by": real,
            "created_at": "2020-01-02T00:00:00+00:00",
            "freeze_basis": settlement_basis(s, p, "CNY"),
            "basis_currencies": {"2020-01-01": ["CNY"]},
            "depends_on": [],
        }
    )
    p = payment_preview(s)
    s["state"]["payer_marks"] = [
        {**p["groups"][0], "id": "mark", "dates": p["dates"], "marked_by": real}
    ]
    return s


def per_person(s, rename=None):
    """What each person owes and is owed, free of 'first member' conventions."""
    rename = rename or {}
    ledger = project(s)
    return {
        "days": {
            d["date"]: (
                d["status"],
                sorted(
                    (
                        b["currency"],
                        rename.get(b["participant_id"], b["participant_id"]),
                        b["amount"],
                    )
                    for b in d["balances"]
                ),
                sorted(
                    (
                        rename.get(t["from_participant_id"], t["from_participant_id"]),
                        rename.get(t["to_participant_id"], t["to_participant_id"]),
                        t["amount"],
                    )
                    for t in d["pending"]
                ),
                len(d["carry_entries"]),
            )
            for d in ledger["days"]
        },
        "payments": [
            (
                p["id"],
                p["amount"],
                rename.get(p["from_participant_id"], p["from_participant_id"]),
                rename.get(p["to_participant_id"], p["to_participant_id"]),
            )
            for p in ledger["payments"]
        ],
    }


def after_claim(s, old, new):
    rewrite = claim(s, old, new)
    payloads = {e["id"]: e["payload"] for e in rewrite["expenses"]}
    return {
        **deepcopy(s),
        "members": sorted(new if m == old else m for m in s["members"]),
        "state": rewrite["state"],
        "expenses": [
            {**e, "payload": payloads.get(e["id"], e["payload"])} for e in s["expenses"]
        ],
    }, rewrite


# The placeholder and the claimer on either side of the real member "m".
@pytest.mark.parametrize("placeholder", ["a", "z"])
@pytest.mark.parametrize("claimer", ["b", "y"])
def test_two_member_claim_keeps_every_amount_whichever_way_the_ids_sort(
    monkeypatch, placeholder, claimer
):
    monkeypatch.setattr("app.core.ledger.business_today", lambda: "2020-01-09")
    before = pair_trip(placeholder)
    original = deepcopy(before)

    after, rewrite = after_claim(before, placeholder, claimer)

    assert before == original
    assert per_person(after) == per_person(before, {placeholder: claimer})
    assert not mentions(after, placeholder)
    # The settled day stays settled and its adjustment logic still works.
    assert per_person(after)["days"]["2020-01-01"][0] == "settled"
    flips = (placeholder < "m") != (claimer < "m")
    paid = rewrite["state"]["payments"][0]
    stored = before["state"]["payments"][0]
    assert paid["amount"] == stored["amount"] == "200.00"
    signs = -1 if flips else 1
    assert [Decimal(a["amount"]) for a in paid["allocations"]] == [
        signs * Decimal(a["amount"]) for a in stored["allocations"]
    ]
    assert [Decimal(a["amount"]) for a in paid["freeze_basis"]] == [
        signs * Decimal(a["amount"]) for a in stored["freeze_basis"]
    ]
    # The open mark still names the transfer that is proposed now.
    mark = rewrite["state"]["payer_marks"][0]
    proposal = payment_preview(after)["groups"][0]
    assert {k: mark[k] for k in proposal} == proposal
    assert [p["id"] for p in rewrite["payments"]] == ["paid"]
    assert rewrite["payments"][0]["allocations"] == paid["allocations"]


@pytest.mark.parametrize("claimer", ["b", "y"])
def test_edit_after_claim_adjusts_the_settled_day_by_the_right_amount(
    monkeypatch, claimer
):
    monkeypatch.setattr("app.core.ledger.business_today", lambda: "2020-01-09")
    after, _ = after_claim(pair_trip("a"), "a", claimer)

    after["expenses"][0]["payload"]["expenses"][0]["items"][0]["amount"]["amount"] = (
        "460"
    )

    days = per_person(after)["days"]
    assert days["2020-01-01"][0] == "settled"
    # 30 more for the settled day joins the 50 already open on the next day.
    assert days["2020-01-02"][2] == [(claimer, "m", "80.00")]


def test_claim_renames_ids_used_as_keys_and_values_everywhere():
    s = pair_trip("a")
    s["members"] = ["a", "m", "x"]
    bill = s["expenses"][1]["payload"]["expenses"][0]
    bill["rounding_owner_id"] = "a"
    bill["items"][0]["allocation_amounts"] = {"a": "50.00", "m": "50.00"}
    bill["adjustments"] = [
        {"adjustment_id": "j", "allocation": {"a": "1"}, "received_by": "a"}
    ]
    s["expenses"][1]["payload"]["someone_elses_field"] = {"nested": ["a", {"a": "a"}]}
    s["state"]["plans"] = [
        {
            "id": "plan",
            "currency": "CNY",
            "dates": ["2020-01-02"],
            "basis": {"2020-01-02": {"a": "-50.00", "m": "50.00"}},
            "transfers": [{"from_participant_id": "a", "to_participant_id": "m"}],
        }
    ]

    rewrite = claim(s, "a", "r")

    assert not mentions(rewrite, "a")
    payload = next(e for e in rewrite["expenses"] if e["id"] == "two")["payload"]
    bill = payload["expenses"][0]
    assert payload["participants"] == ["m", "r"]
    assert bill["rounding_owner_id"] == "r"
    assert bill["items"][0]["allocation"] == {"m": "0.5", "r": "0.5"}
    assert bill["items"][0]["allocation_amounts"] == {"r": "50.00", "m": "50.00"}
    assert bill["adjustments"][0] == {
        "adjustment_id": "j",
        "allocation": {"r": "1"},
        "received_by": "r",
    }
    assert payload["someone_elses_field"] == {"nested": ["r", {"r": "r"}]}
    assert rewrite["state"]["plans"][0]["basis"] == {
        "2020-01-02": {"r": "-50.00", "m": "50.00"}
    }
    # Text that merely contains the id is left alone.
    assert renamed({"title": "a b", "note": ["aa", "a"]}, "a", "r") == {
        "title": "a b",
        "note": ["aa", "r"],
    }


def test_member_format_records_are_renamed_without_touching_amounts():
    s = pair_trip("a")
    s["members"] = ["a", "m", "x"]
    s["state"]["payments"] = [
        {
            "id": "v2",
            "format_version": 2,
            "currency": "CNY",
            "amount": "50.00",
            "from_participant_id": "a",
            "to_participant_id": "x",
            "allocations": [{"date": "2020-01-02", "amount": "50.00"}],
            "payer_allocations": [{"date": "2020-01-02", "amount": "-50.00"}],
            "freeze_basis": [
                {
                    "date": "2020-01-02",
                    "currency": "CNY",
                    "participant_id": "a",
                    "amount": "-50.00",
                }
            ],
        },
        {
            "id": "offset",
            "format_version": 2,
            "currency": "CNY",
            "amount": "0.00",
            "from_participant_id": None,
            "to_participant_id": "a",
            "allocations": [{"date": "2020-01-02", "amount": "0.00"}],
            "payer_allocations": [],
            "freeze_basis": [],
            "offset_only": True,
        },
    ]
    s["state"]["payer_marks"] = []
    expected = renamed(deepcopy(s["state"]["payments"]), "a", "zz")

    rewrite = claim(s, "a", "zz")

    assert rewrite["state"]["payments"] == expected
    assert [(p["id"], p["from_id"], p["to_id"]) for p in rewrite["payments"]] == [
        ("v2", "zz", "x"),
        ("offset", None, "zz"),
    ]


def test_claim_is_refused_for_a_member_or_a_vanished_placeholder_or_bad_history():
    s = pair_trip("a")
    with pytest.raises(ClaimRefused):
        claim(s, "a", "m")
    with pytest.raises(ClaimRefused):
        claim(s, "gone", "r")
    del s["state"]["payments"][0]["from_participant_id"]
    with pytest.raises(ClaimRefused):
        claim(s, "a", "r")


def test_removal_ignores_being_listed_but_not_paying_owing_or_settling():
    s = snapshot()
    s["members"] = ["a", "b", "p"]
    s["expenses"][0]["payload"]["participants"] = ["a", "b", "p"]
    listed_only = deepcopy(s)

    plan = removal(s, "p")

    assert s == listed_only
    assert plan == {
        "expenses": [
            {
                "id": "old",
                "payload": {**s["expenses"][0]["payload"], "participants": ["a", "b"]},
            }
        ]
    }
    bill = lambda t: t["expenses"][0]["payload"]["expenses"][0]  # noqa: E731
    owes_nothing = deepcopy(s)
    bill(owes_nothing)["items"][0]["allocation"] = {"a": "1", "p": "0"}
    payer = deepcopy(s)
    bill(payer)["payer_id"] = "p"
    receiver = deepcopy(s)
    bill(receiver)["adjustments"] = [{"allocation": {"a": "1"}, "received_by": "p"}]
    rounding = deepcopy(s)
    bill(rounding)["rounding_owner_id"] = "p"
    revoked = deepcopy(s)
    revoked["state"]["payments"] = [
        {"id": "x", "to_participant_id": "p", "revoked_at": "t"}
    ]
    planned = deepcopy(s)
    planned["state"]["plans"] = [{"basis": {"2020-01-01": {"p": "0.00"}}}]
    for used in (owes_nothing, payer, receiver, rounding, revoked, planned):
        assert removal(used, "p") is None
    assert removal(s, "nobody") == {"expenses": []}
