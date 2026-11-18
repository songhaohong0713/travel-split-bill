"""Random multi-member lifecycles: books stay balanced and can always be closed."""

import os
import random
from decimal import Decimal

import pytest
from test_ledger import auth
from test_ledger import ledger as ledger  # noqa: PLC0414
from test_multi_member_settlement import join, respend, scope, spend, view

DAYS = ["2026-10-05", "2026-10-06", "2026-10-07"]
ROUNDS = int(os.environ.get("LEDGER_LIFECYCLES", "6"))


def act(c, base, transfer, p, by, endpoint="/payments", **extra):
    body = {"dates": p["dates"], "token": p["token"], "currency": "CNY", **extra}
    if transfer.get("offset_only"):
        body["offset_participant_id"] = transfer["to_participant_id"]
    else:
        body.update(
            from_participant_id=transfer["from_participant_id"],
            to_participant_id=transfer["to_participant_id"],
            plan_id=transfer["plan_id"],
        )
    return c.post(base + endpoint, headers=auth(by), json=body)


def assert_balanced(c, base, members, shares):
    """Sum of a member's daily balances = spending net - received + paid."""
    data, days = view(c, base)
    expected = dict.fromkeys(members, Decimal(0))
    for payer, split in shares.values():
        for member, value in split.items():
            expected[member] -= Decimal(value)
            expected[payer] += Decimal(value)
    for payment in data["payments"]:
        if not payment.get("revoked_at") and not payment.get("offset_only"):
            expected[payment["to_participant_id"]] -= Decimal(payment["amount"])
            expected[payment["from_participant_id"]] += Decimal(payment["amount"])
    actual = dict.fromkeys(members, Decimal(0))
    for day in days.values():
        assert day["status"] in ("pending", "settled"), day
        assert (day["status"] == "pending") == bool(day["balances"])
        for balance in day["balances"]:
            actual[balance["participant_id"]] += Decimal(balance["amount"])
    assert actual == expected
    return data


@pytest.mark.parametrize("seed", range(ROUNDS))
def test_random_lifecycle_stays_balanced_and_can_be_closed(ledger, seed):
    rng = random.Random(seed)
    c, base = ledger
    members = ["a", "b", *"cdef"[: rng.randint(1, 4)]]
    join(base, *members[2:])
    saved, shares = {}, {}

    def record():
        payer = rng.choice(members)
        people = rng.sample(members, rng.randint(1, len(members)))
        split = {m: str(rng.choice([50, 100, 100, 150])) for m in people}
        row, body = spend(c, base, split, payer=payer, day=rng.choice(DAYS))
        saved[row["id"]] = (row, body, 1)
        shares[row["id"]] = (payer, split)

    for _ in range(rng.randint(2, 5)):
        record()
    for _ in range(30):
        roll = rng.random()
        dates = rng.choice([None, None, rng.sample(DAYS, rng.randint(1, 3))])
        if roll < 0.12:
            record()
        elif roll < 0.24:
            key = rng.choice(sorted(saved))
            row, body, revision = saved[key]
            payer, split = shares[key]
            split = {m: str(rng.choice([50, 100, 150])) for m in split}
            respend(c, base, row, body, split, revision)
            saved[key] = (row, body, revision + 1)
            shares[key] = (payer, split)
        elif roll < 0.34:
            active = [
                p for p in view(c, base)[0]["payments"] if not p.get("revoked_at")
            ]
            if active:
                r = c.post(
                    base + "/payments/" + rng.choice(active)["id"] + "/revoke",
                    headers=auth(),
                )
                assert r.status_code in (200, 409), r.text
        else:
            r = c.post(
                base + "/payments/preview", headers=auth(), json={"dates": dates}
            )
            assert r.status_code in (200, 422), r.text
            transfers = r.json().get("data", {}).get("transfers")
            if transfers:
                transfer = rng.choice(transfers)
                if roll < 0.6 and not transfer.get("offset_only"):
                    r = act(
                        c,
                        base,
                        transfer,
                        r.json()["data"],
                        transfer["from_participant_id"],
                        "/payments/mark-paid",
                    )
                else:
                    r = act(
                        c,
                        base,
                        transfer,
                        r.json()["data"],
                        transfer["to_participant_id"],
                    )
                assert r.status_code in (201, 409), r.text
        data = assert_balanced(c, base, members, shares)
        # A mark that is still valid can always be confirmed by its recipient.
        for mark in data["payer_marks"]:
            if not mark["stale"]:
                assert scope(c, base, mark["dates"])["conflicts"] == []

    # Whatever happened, confirming everything proposed closes every day.
    for _ in range(40):
        p = scope(c, base)
        if not p["transfers"]:
            break
        transfer = p["transfers"][0]
        r = act(c, base, transfer, p, transfer["to_participant_id"])
        assert r.status_code == 201, r.text
    data = assert_balanced(c, base, members, shares)
    assert all(day["status"] == "settled" for day in data["days"]), data["days"]
