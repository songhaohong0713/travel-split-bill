"""Per-member daily allocation of a multi-day, multi-member settlement plan."""

import random
from decimal import Decimal

from app.core.ledger import _plan_transfers

DAYS = ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"]


def applied(net, transfers):
    left = {day: dict(values) for day, values in net.items()}
    for transfer in transfers:
        for member, key in (
            (transfer["to_participant_id"], "allocations"),
            (transfer["from_participant_id"], "payer_allocations"),
        ):
            rows = transfer[key]
            assert len({row["date"] for row in rows}) == len(rows)
            signed = Decimal(transfer["amount"]) * (1 if key == "allocations" else -1)
            assert sum(Decimal(row["amount"]) for row in rows) == signed
            for row in rows:
                left[row["date"]][member] -= Decimal(row["amount"])
    return left


def test_days_running_against_a_transfer_are_absorbed_by_it():
    net = {
        DAYS[0]: {"a": Decimal(150), "b": Decimal(-150)},
        DAYS[1]: {"a": Decimal(-50), "b": Decimal(50)},
    }

    transfers = _plan_transfers("CNY", net)

    assert transfers == [
        {
            "currency": "CNY",
            "amount": "100.00",
            "from_participant_id": "b",
            "to_participant_id": "a",
            "allocations": [
                {"date": DAYS[0], "amount": "150.00"},
                {"date": DAYS[1], "amount": "-50.00"},
            ],
            "payer_allocations": [
                {"date": DAYS[0], "amount": "-150.00"},
                {"date": DAYS[1], "amount": "50.00"},
            ],
        }
    ]


def test_a_payer_with_two_recipients_splits_days_oldest_first():
    net = {
        DAYS[0]: {"a": Decimal(120), "c": Decimal(-120)},
        DAYS[1]: {"b": Decimal(80), "c": Decimal(-80)},
    }

    first, second = _plan_transfers("CNY", net)

    assert (first["to_participant_id"], first["payer_allocations"]) == (
        "a",
        [{"date": DAYS[0], "amount": "-120.00"}],
    )
    assert (second["to_participant_id"], second["payer_allocations"]) == (
        "b",
        [{"date": DAYS[1], "amount": "-80.00"}],
    )


def test_balanced_ranges_are_cleared_on_every_day_for_every_member():
    rng = random.Random(20261118)
    for _ in range(300):
        members = "abcdef"[: rng.randint(3, 6)]
        net = {}
        for day in DAYS[: rng.randint(1, 4)]:
            values = [Decimal(rng.randint(-500, 500)) for _ in members[1:]]
            net[day] = dict(zip(members, [-sum(values), *values]))
        # A member whose days cancel out to exactly zero takes part in no
        # transfer; how those days get settled is an open product question.
        if any(not sum(net[day][m] for day in net) for m in members):
            continue

        left = applied(net, _plan_transfers("CNY", net))

        assert all(not value for values in left.values() for value in values.values())


def test_only_the_part_that_balances_gets_transfers():
    net = {DAYS[0]: {"a": Decimal(120), "c": Decimal(-110)}}

    transfers = _plan_transfers("CNY", net)

    assert [t["amount"] for t in transfers] == ["110.00"]
    assert applied(net, transfers) == {DAYS[0]: {"a": Decimal(10), "c": Decimal(0)}}
