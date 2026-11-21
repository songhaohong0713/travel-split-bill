"""Phase 2 acceptance: settlement between more than two trip members."""

from uuid import uuid4

from app.db.models import TripLedger, TripMember, User
from app.db.session import SessionLocal
from test_ledger import auth, expense
from test_ledger import ledger as ledger  # noqa: PLC0414

D1, D2 = "2026-10-07", "2026-10-08"


def join(base, *users):
    """Invites are still capped at two until phase 3, so add members directly."""
    with SessionLocal() as session:
        for user in users:
            if session.get(User, user) is None:
                session.add(User(id=user, wechat_openid_hash=None))
            session.add(TripMember(trip_id=base.rsplit("/", 1)[1], user_id=user))
        session.commit()


def spend(c, base, shares, payer="a", day=D1):
    """One line item per person: everyone owes exactly their own share."""
    body = {
        "occurred_at": day,
        "payload": {
            "title": "晚餐",
            "participants": sorted({*shares, payer}),
            "expenses": [
                {
                    "expense_id": str(uuid4()),
                    "payer_id": payer,
                    "settlement_currency": "CNY",
                    "items": [
                        {
                            "item_id": person,
                            "amount": {"currency": "CNY", "amount": value},
                            "allocation": {person: "1"},
                        }
                        for person, value in sorted(shares.items())
                    ],
                }
            ],
        },
    }
    r = c.post(
        base + "/expenses",
        headers={**auth(), "Idempotency-Key": str(uuid4())},
        json=body,
    )
    assert r.status_code == 201, r.text
    return r.json()["data"], body


def respend(c, base, saved, body, shares, revision=1):
    for item in body["payload"]["expenses"][0]["items"]:
        item["amount"]["amount"] = shares[item["item_id"]]
    r = c.patch(
        base + "/expenses/" + saved["id"],
        headers=auth(),
        json={**body, "revision": revision},
    )
    assert r.status_code == 200, r.text


def scope(c, base, dates=None, user="a"):
    r = c.post(base + "/payments/preview", headers=auth(user), json={"dates": dates})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def pairs(transfers):
    return [
        (t["from_participant_id"], t["to_participant_id"], t["amount"])
        for t in transfers
    ]


def request(p, payer, payee, **extra):
    return {
        "dates": p["dates"],
        "token": p["token"],
        "currency": "CNY",
        "from_participant_id": payer,
        "to_participant_id": payee,
        **extra,
    }


def mark(c, base, payer, payee, dates=None, by=None):
    p = scope(c, base, dates)
    return c.post(
        base + "/payments/mark-paid",
        headers=auth(by or payer),
        json=request(p, payer, payee),
    )


def confirm(c, base, payer, payee, dates=None, by=None, **extra):
    p = scope(c, base, dates)
    return c.post(
        base + "/payments",
        headers=auth(by or payee),
        json=request(p, payer, payee, **extra),
    )


def view(c, base, user="a"):
    data = c.get(base + "/ledger", headers=auth(user)).json()["data"]
    return data, {d["date"]: d for d in data["days"]}


def three(ledger):
    c, base = ledger
    join(base, "c")
    saved, body = spend(c, base, {"a": "100", "b": "100", "c": "100"})
    return c, base, saved, body


def test_three_members_equal_split_needs_two_transfers(ledger):
    c, base, _, _ = three(ledger)

    p = scope(c, base)

    assert p["dates"] == [D1]
    assert "groups" not in p
    assert pairs(p["transfers"]) == [("b", "a", "100.00"), ("c", "a", "100.00")]
    assert p["transfers"][0]["allocations"] == [{"date": D1, "amount": "100.00"}]
    assert p["transfers"][0]["payer_allocations"] == [{"date": D1, "amount": "-100.00"}]
    data, days = view(c, base)
    assert pairs(data["summary"]) == pairs(p["transfers"])
    assert [(*pairs([t]), t["status"]) for t in days[D1]["transfers"]] == [
        (("b", "a", "100.00"), "pending"),
        (("c", "a", "100.00"), "pending"),
    ]


def test_two_member_preview_keeps_groups_and_mirrors_them_as_transfers(ledger):
    c, base = ledger
    expense(c, base)

    p = scope(c, base)

    assert p["groups"][0]["amount"] == "200.00"
    assert p["transfers"] == p["groups"]
    body = {"dates": p["dates"], "token": p["token"], "currency": "CNY"}
    wrong = {**body, "from_participant_id": "a", "to_participant_id": "b"}
    assert (
        c.post(base + "/payments/mark-paid", headers=auth("a"), json=wrong).status_code
        == 422
    )
    named = {**body, "from_participant_id": "b", "to_participant_id": "a"}
    assert (
        c.post(base + "/payments/mark-paid", headers=auth("b"), json=named).status_code
        == 201
    )


def test_multi_member_requests_must_name_the_pair_and_respect_roles(ledger):
    c, base, _, _ = three(ledger)
    p = scope(c, base)
    bare = {"dates": p["dates"], "token": p["token"], "currency": "CNY"}

    assert (
        c.post(base + "/payments/mark-paid", headers=auth("b"), json=bare).status_code
        == 422
    )
    assert c.post(base + "/payments", headers=auth("a"), json=bare).status_code == 422
    # Only the payer marks, only the recipient confirms.
    assert mark(c, base, "b", "a", by="c").status_code == 403
    assert mark(c, base, "b", "a", by="a").status_code == 403
    assert confirm(c, base, "b", "a", by="b").status_code == 403
    assert confirm(c, base, "b", "a", by="c").status_code == 403
    assert mark(c, base, "c", "b").status_code == 422
    # The recipient may confirm without waiting for a mark.
    assert confirm(c, base, "b", "a").status_code == 201


def test_confirming_one_marked_transfer_keeps_the_other_mark_valid(ledger):
    c, base, _, _ = three(ledger)
    mark_b = mark(c, base, "b", "a")
    mark_c = mark(c, base, "c", "a")
    assert mark_b.status_code == mark_c.status_code == 201, mark_c.text

    paid = confirm(c, base, "b", "a", mark_id=mark_b.json()["data"]["id"])

    assert paid.status_code == 201, paid.text
    assert paid.json()["data"]["format_version"] == 2
    data, days = view(c, base)
    marks = {m["id"]: m for m in data["payer_marks"]}
    assert marks[mark_c.json()["data"]["id"]]["stale"] is False
    assert marks[mark_b.json()["data"]["id"]]["confirmed_payment_id"]
    assert days[D1]["status"] == "pending"
    assert [(*pairs([t]), t["status"]) for t in days[D1]["transfers"]] == [
        (("b", "a", "100.00"), "settled"),
        (("c", "a", "100.00"), "marked"),
    ]
    assert pairs(scope(c, base)["transfers"]) == [("c", "a", "100.00")]

    last = confirm(c, base, "c", "a", mark_id=mark_c.json()["data"]["id"])

    assert last.status_code == 201, last.text
    data, days = view(c, base)
    assert days[D1]["status"] == "settled"
    assert days[D1]["balances"] == []
    assert data["summary"] == []


def test_new_mark_only_supersedes_marks_of_the_same_pair(ledger):
    c, base, _, _ = three(ledger)
    first_b = mark(c, base, "b", "a").json()["data"]
    first_c = mark(c, base, "c", "a").json()["data"]

    second_b = mark(c, base, "b", "a").json()["data"]

    marks = {m["id"]: m for m in view(c, base)[0]["payer_marks"]}
    assert marks[first_b["id"]]["revoke_reason"] == "superseded"
    assert not marks[first_c["id"]].get("revoked_at")
    assert marks[first_c["id"]]["stale"] is False
    assert marks[second_b["id"]]["stale"] is False


def settle_b_then_raise_to_330(ledger):
    c, base, saved, body = three(ledger)
    mark_b = mark(c, base, "b", "a").json()["data"]
    mark_c = mark(c, base, "c", "a").json()["data"]
    assert confirm(c, base, "b", "a", mark_id=mark_b["id"]).status_code == 201
    respend(c, base, saved, body, {"a": "110", "b": "110", "c": "110"})
    return c, base, mark_c


def test_edit_after_one_settlement_freezes_only_the_settled_member(ledger):
    c, base, mark_c = settle_b_then_raise_to_330(ledger)

    data, days = view(c, base)

    # b is frozen on the day: the extra 10 rolls to b's next unfrozen date.
    assert days[D2]["carry_entries"] == [
        {
            "kind": "adjustment",
            "participant_id": "b",
            "source_date": D1,
            "currency": "CNY",
            "signed_amount": "-10.00",
            "amount": "10.00",
        }
    ]
    assert days[D2]["status"] == "pending"
    assert days[D2]["balances"] == [
        {"currency": "CNY", "participant_id": "b", "amount": "-10.00"}
    ]
    # The day keeps c -> a 110; a holds 10 more with no matching transfer.
    assert days[D1]["status"] == "pending"
    assert days[D1]["balances"] == [
        {"currency": "CNY", "participant_id": "a", "amount": "120.00"},
        {"currency": "CNY", "participant_id": "c", "amount": "-110.00"},
    ]
    assert pairs(scope(c, base, [D1])["transfers"]) == [("c", "a", "110.00")]
    # The unconfirmed mark was made against the old plan and is void.
    marks = {m["id"]: m for m in data["payer_marks"]}
    assert marks[mark_c["id"]]["stale"] is True
    assert confirm(c, base, "c", "a", [D1], mark_id=mark_c["id"]).status_code == 409
    # Settling the day together with the adjustment date balances everyone.
    assert pairs(scope(c, base, [D1, D2])["transfers"]) == [
        ("c", "a", "110.00"),
        ("b", "a", "10.00"),
    ]


def test_day_with_unmatched_balance_stays_pending_without_transfers(ledger):
    c, base, _ = settle_b_then_raise_to_330(ledger)

    assert confirm(c, base, "c", "a", [D1]).status_code == 201

    data, days = view(c, base)
    assert days[D1]["status"] == "pending"
    assert days[D1]["pending"] == []
    # The only open transfer touching the day spans the adjustment date too.
    assert [(t["status"], t["dates"]) for t in days[D1]["transfers"]] == [
        ("settled", [D1]),
        ("settled", [D1]),
        ("pending", [D1, D2]),
    ]
    assert days[D1]["balances"] == [
        {"currency": "CNY", "participant_id": "a", "amount": "10.00"}
    ]
    assert scope(c, base, [D1])["transfers"] == []
    assert pairs(data["summary"]) == [("b", "a", "10.00")]

    last = confirm(c, base, "b", "a", [D1, D2])

    assert last.status_code == 201, last.text
    assert last.json()["data"]["allocations"] == [{"date": D1, "amount": "10.00"}]
    assert last.json()["data"]["payer_allocations"] == [
        {"date": D2, "amount": "-10.00"}
    ]
    data, days = view(c, base)
    assert {d: days[d]["status"] for d in days} == {D1: "settled", D2: "settled"}
    assert data["summary"] == []


def four(ledger):
    c, base = ledger
    join(base, "c", "d")
    spend(c, base, {"c": "90", "d": "10"}, payer="a")
    spend(c, base, {"d": "80"}, payer="b")
    return c, base


def test_settling_one_of_four_members_transfers_keeps_the_others(ledger):
    c, base = four(ledger)
    assert pairs(scope(c, base)["transfers"]) == [
        ("c", "a", "90.00"),
        ("d", "a", "10.00"),
        ("d", "b", "80.00"),
    ]

    assert confirm(c, base, "d", "a").status_code == 201

    assert pairs(scope(c, base)["transfers"]) == [
        ("c", "a", "90.00"),
        ("d", "b", "80.00"),
    ]
    # d still owes b on the day, so d is not frozen yet.
    _, days = view(c, base)
    assert days[D1]["balances"] == [
        {"currency": "CNY", "participant_id": "a", "amount": "90.00"},
        {"currency": "CNY", "participant_id": "b", "amount": "80.00"},
        {"currency": "CNY", "participant_id": "c", "amount": "-90.00"},
        {"currency": "CNY", "participant_id": "d", "amount": "-80.00"},
    ]


def test_fixed_plan_is_not_repaired_after_a_settlement(ledger):
    c, base = ledger
    join(base, "c", "d", "e")
    spend(c, base, {"d": "70", "c": "30"}, payer="a")
    spend(c, base, {"c": "30", "e": "60"}, payer="b")
    plan = [
        ("d", "a", "70.00"),
        ("c", "a", "30.00"),
        ("c", "b", "30.00"),
        ("e", "b", "60.00"),
    ]
    assert pairs(scope(c, base)["transfers"]) == plan

    assert mark(c, base, "e", "b").status_code == 201
    assert confirm(c, base, "d", "a").status_code == 201

    # A fresh pairing of the rest would be c->b 60, e->b 30, e->a 30.
    assert pairs(scope(c, base)["transfers"]) == plan[1:]
    marks = view(c, base)[0]["payer_marks"]
    assert [m["stale"] for m in marks] == [False]


def test_revoking_a_settlement_another_depends_on_is_rejected(ledger):
    c, base, _, _ = three(ledger)
    first = confirm(c, base, "b", "a").json()["data"]
    second = confirm(c, base, "c", "a").json()["data"]
    assert second["depends_on"] == [first["id"]]

    blocked = c.post(base + "/payments/" + first["id"] + "/revoke", headers=auth())

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "PAYMENT_DEPENDENCY"
    assert blocked.json()["error"]["dates"] == [D1]
    assert (
        c.post(
            base + "/payments/" + second["id"] + "/revoke", headers=auth()
        ).status_code
        == 200
    )
    assert (
        c.post(
            base + "/payments/" + first["id"] + "/revoke", headers=auth()
        ).status_code
        == 200
    )
    assert pairs(scope(c, base)["transfers"]) == [
        ("b", "a", "100.00"),
        ("c", "a", "100.00"),
    ]


def test_settlements_between_unrelated_members_do_not_depend_on_each_other(ledger):
    c, base = ledger
    join(base, "c", "d")
    spend(c, base, {"b": "50"}, payer="a")
    spend(c, base, {"d": "70"}, payer="c")

    first = confirm(c, base, "d", "c").json()["data"]
    second = confirm(c, base, "b", "a").json()["data"]

    assert second["depends_on"] == []
    assert (
        c.post(
            base + "/payments/" + first["id"] + "/revoke", headers=auth()
        ).status_code
        == 200
    )


def test_home_summary_ignores_transfers_between_other_members(ledger):
    c, base = ledger
    join(base, "c")
    spend(c, base, {"a": "100", "b": "100"})

    bystander = c.get("/v1/trips/ledger-summary", headers=auth("c")).json()["data"]
    payer = c.get("/v1/trips/ledger-summary", headers=auth("b")).json()["data"]

    assert bystander["groups"] == []
    assert bystander["trips"][0]["pending"] == []
    assert bystander["trips"][0]["pending_days"] == 1
    assert payer["groups"] == [
        {"currency": "CNY", "receivable": "0.00", "payable": "100.00"}
    ]
    assert payer["trips"][0]["pending"][0]["direction"] == "payable"


def test_home_summary_survives_one_broken_trip(ledger):
    c, base = ledger
    expense(c, base)
    broken = c.post(
        "/v1/trips", headers=auth(), json={"name": "坏账本", "default_currency": "CNY"}
    ).json()["data"]["id"]
    with SessionLocal() as session:
        session.add(
            TripLedger(
                trip_id=broken, revision=1, state_json={"payments": [{"id": "x"}]}
            )
        )
        session.commit()

    response = c.get("/v1/trips/ledger-summary", headers=auth())

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    trips = {t["id"]: t for t in data["trips"]}
    assert trips[broken]["pending"] == []
    assert trips[broken]["incomplete"] is True
    good = trips[base.rsplit("/", 1)[1]]
    assert good["incomplete"] is False
    assert good["pending"][0]["amount"] == "200.00"
    assert data["groups"] == [
        {"currency": "CNY", "receivable": "200.00", "payable": "0.00"}
    ]


def test_member_joining_a_settled_two_person_day_is_frozen_with_the_pair(ledger):
    c, base = ledger
    expense(c, base)
    p = scope(c, base)
    paid = c.post(
        base + "/payments",
        headers=auth(),
        json={"dates": p["dates"], "token": p["token"], "currency": "CNY"},
    )
    assert paid.status_code == 201
    assert "format_version" not in paid.json()["data"]
    join(base, "c")

    spend(c, base, {"a": "100", "b": "100", "c": "100"})

    _, days = view(c, base)
    assert days[D1]["status"] == "settled"
    assert days[D1]["balances"] == []
    assert sorted(
        (e["participant_id"], e["signed_amount"]) for e in days[D2]["carry_entries"]
    ) == [("a", "200.00"), ("b", "-100.00"), ("c", "-100.00")]
    assert pairs(scope(c, base)["transfers"]) == [
        ("b", "a", "100.00"),
        ("c", "a", "100.00"),
    ]


def test_history_confirmation_is_two_person_only(ledger):
    c, base, _, _ = three(ledger)
    assert c.post(base + "/history/prepare", headers=auth()).status_code == 200

    r = c.post(base + "/history/preview", headers=auth(), json={"mode": "unpaid"})

    assert r.status_code == 422
    assert "两人" in r.json()["error"]["message"]


def test_day_close_endpoint_is_gone(ledger):
    c, base = ledger
    expense(c, base)

    assert c.post(base + "/days/" + D1 + "/close", headers=auth()).status_code in (
        404,
        405,
    )


def offset(c, base, member, dates=None, by="c"):
    p = scope(c, base, dates)
    return c.post(
        base + "/payments",
        headers=auth(by),
        json={
            "dates": p["dates"],
            "token": p["token"],
            "currency": "CNY",
            "offset_participant_id": member,
        },
    )


def offsets(transfers):
    return [
        (t["to_participant_id"], t["allocations"])
        for t in transfers
        if t.get("offset_only")
    ]


def test_members_whose_days_cancel_out_get_an_offset_each(ledger):
    c, base = ledger
    join(base, "c")
    spend(c, base, {"b": "100"}, payer="a", day=D1)
    spend(c, base, {"a": "100"}, payer="b", day=D2)

    p = scope(c, base)

    assert pairs(p["transfers"]) == [(None, "a", "0.00"), (None, "b", "0.00")]
    assert offsets(p["transfers"]) == [
        ("a", [{"date": D1, "amount": "100.00"}, {"date": D2, "amount": "-100.00"}]),
        ("b", [{"date": D1, "amount": "-100.00"}, {"date": D2, "amount": "100.00"}]),
    ]
    # An offset moves no money: it cannot be marked as paid.
    body = {"dates": p["dates"], "token": p["token"], "currency": "CNY"}
    marked = c.post(
        base + "/payments/mark-paid",
        headers=auth("a"),
        json={**body, "offset_participant_id": "a"},
    )
    assert marked.status_code == 422
    # Any member may confirm it.
    first = offset(c, base, "a", by="c")
    assert first.status_code == 201, first.text
    assert first.json()["data"]["format_version"] == 2
    assert first.json()["data"]["from_participant_id"] is None
    _, days = view(c, base)
    assert {d: days[d]["status"] for d in days} == {D1: "pending", D2: "pending"}
    assert offset(c, base, "b", by="a").status_code == 201

    data, days = view(c, base)
    assert {d: days[d]["status"] for d in days} == {D1: "settled", D2: "settled"}
    assert data["summary"] == []
    revoked = c.post(
        base + "/payments/" + first.json()["data"]["id"] + "/revoke", headers=auth()
    )
    assert revoked.status_code == 200
    assert offsets(scope(c, base)["transfers"])[0][0] == "a"


def test_offset_sits_next_to_the_transfer_that_passes_through_a_member(ledger):
    c, base = ledger
    join(base, "c")
    spend(c, base, {"b": "100"}, payer="a", day=D1)
    spend(c, base, {"a": "100"}, payer="c", day=D2)

    p = scope(c, base)

    assert pairs(p["transfers"]) == [("b", "c", "100.00"), (None, "a", "0.00")]
    assert confirm(c, base, "b", "c").status_code == 201
    _, days = view(c, base)
    assert {d: days[d]["status"] for d in days} == {D1: "pending", D2: "pending"}
    assert offset(c, base, "a").status_code == 201

    data, days = view(c, base)
    assert {d: days[d]["status"] for d in days} == {D1: "settled", D2: "settled"}
    assert data["summary"] == []


def two_days(ledger):
    c, base = ledger
    join(base, "c")
    spend(c, base, {"a": "100", "b": "100", "c": "100"}, payer="a", day=D1)
    spend(c, base, {"a": "100", "b": "100", "c": "100"}, payer="b", day=D2)
    return c, base


def test_a_marked_transfer_survives_settlements_over_a_wider_range(ledger):
    c, base = two_days(ledger)
    # Paired on their own, both days together need only c->a and c->b.
    assert pairs(scope(c, base)["transfers"]) == [
        ("c", "a", "100.00"),
        ("c", "b", "100.00"),
    ]
    marked = mark(c, base, "b", "a", [D1])
    assert marked.status_code == 201, marked.text
    mark_id = marked.json()["data"]["id"]

    everything = scope(c, base)

    assert everything["conflicts"] == []
    assert pairs(everything["transfers"]) == [
        ("b", "a", "100.00"),
        ("c", "a", "100.00"),
        ("a", "b", "100.00"),
        ("c", "b", "100.00"),
    ]
    data, days = view(c, base)
    assert [(*pairs([t]), t["status"]) for t in days[D1]["transfers"]] == [
        (("b", "a", "100.00"), "marked"),
        (("c", "a", "100.00"), "pending"),
    ]
    assert pairs(data["summary"]) == pairs(everything["transfers"])

    assert confirm(c, base, "c", "a").status_code == 201
    assert confirm(c, base, "c", "b").status_code == 201

    marks = {m["id"]: m for m in view(c, base)[0]["payer_marks"]}
    assert marks[mark_id]["stale"] is False
    assert confirm(c, base, "b", "a", mark_id=mark_id).status_code == 201
    assert confirm(c, base, "a", "b").status_code == 201
    data, days = view(c, base)
    assert {d: days[d]["status"] for d in days} == {D1: "settled", D2: "settled"}


def test_range_cutting_through_a_marked_plan_cannot_be_settled(ledger):
    c, base = two_days(ledger)
    assert mark(c, base, "c", "a").status_code == 201

    part = scope(c, base, [D1])

    assert part["conflicts"] == [{"currency": "CNY", "dates": [D1, D2]}]
    blocked = confirm(c, base, "b", "a", [D1])
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "MARKED_TRANSFER_PENDING"
    assert blocked.json()["error"]["dates"] == [D1, D2]
    assert mark(c, base, "b", "a", [D1]).status_code == 409
    assert view(c, base)[0]["payments"] == []
    # The whole range is still open, exactly as it was marked.
    assert pairs(scope(c, base)["transfers"]) == [
        ("c", "a", "100.00"),
        ("c", "b", "100.00"),
    ]


def test_a_plan_nobody_stands_behind_gives_way_to_a_new_range(ledger):
    c, base = two_days(ledger)
    marked = mark(c, base, "c", "a").json()["data"]
    assert (
        c.post(
            base + "/payer-marks/" + marked["id"] + "/revoke", headers=auth("c")
        ).status_code
        == 200
    )

    assert scope(c, base, [D1])["conflicts"] == []
    assert confirm(c, base, "b", "a", [D1]).status_code == 201


def test_adjustment_rolling_into_today_leaves_a_remainder_until_tomorrow(ledger):
    from datetime import UTC, datetime, timedelta

    today = datetime.now(UTC).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    c, base = ledger
    join(base, "c")
    saved, body = spend(c, base, {"a": "100", "b": "100", "c": "100"}, day=yesterday)
    assert confirm(c, base, "b", "a").status_code == 201

    respend(c, base, saved, body, {"a": "110", "b": "110", "c": "110"})

    # b's extra 10 lands on today, which cannot be settled yet.
    data, days = view(c, base)
    assert days[today.isoformat()]["status"] == "recording"
    assert [
        (e["participant_id"], e["signed_amount"])
        for e in days[today.isoformat()]["carry_entries"]
    ] == [("b", "-10.00")]
    assert pairs(data["summary"]) == [("c", "a", "110.00")]
    assert confirm(c, base, "c", "a").status_code == 201
    data, days = view(c, base)
    assert days[yesterday]["status"] == "pending"
    assert days[yesterday]["balances"] == [
        {"currency": "CNY", "participant_id": "a", "amount": "10.00"}
    ]
    assert data["summary"] == []
    summary = c.get("/v1/trips/ledger-summary", headers=auth()).json()["data"]
    assert summary["trips"][0]["pending"] == []
    assert summary["trips"][0]["pending_days"] == 1


def test_third_member_may_edit_but_not_delete_someone_elses_expense(ledger):
    c, base = ledger
    join(base, "c")
    saved, body = spend(c, base, {"a": "100", "b": "100", "c": "100"})
    path = base + "/expenses/" + saved["id"]

    body["payload"]["title"] = "改过的晚餐"
    edited = c.patch(path, headers=auth("c"), json={**body, "revision": 1})

    assert edited.status_code == 200, edited.text
    assert c.delete(path, headers=auth("c")).status_code == 404
    assert c.delete(path, headers=auth("b")).status_code == 404
    assert c.delete(path, headers=auth()).status_code == 204
