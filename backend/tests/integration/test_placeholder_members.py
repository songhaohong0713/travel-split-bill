"""Phase 5: members who do not use the app, and claiming them."""

from uuid import uuid4

import pytest
from app.db.models import (
    ExpenseRecord,
    PaymentAllocationRecord,
    PaymentRecord,
    TripLedger,
    TripMember,
    User,
)
from app.db.session import SessionLocal
from sqlalchemy import select
from test_ledger import auth, expense
from test_ledger import ledger as ledger  # noqa: PLC0414
from test_multi_member_settlement import (
    D1,
    D2,
    confirm,
    join,
    mark,
    pairs,
    scope,
    spend,
    view,
)

TRIP = lambda base: base.rsplit("/", 1)[1]  # noqa: E731


def add(c, base, name="小王", by="a"):
    return c.post(base + "/placeholders", headers=auth(by), json={"nickname": name})


def members(c, base, user="a"):
    return c.get(base + "/members", headers=auth(user)).json()["data"]


def invite(c, base):
    return c.post(base + "/invites", headers=auth()).json()["data"]["token"]


def claim(c, token, placeholder, user):
    return c.post(
        f"/v1/trip-invites/{token}/claim",
        headers=auth(user),
        json={"placeholder_id": placeholder},
    )


def placeholder_trip(ledger):
    """a and b are real; p is a placeholder added by b."""
    c, base = ledger
    p = add(c, base, by="b").json()["data"]["id"]
    return c, base, p


def test_any_member_adds_a_placeholder_by_name_and_it_counts_toward_six(ledger):
    c, base = ledger

    added = add(c, base, "  小王 ", by="b")

    assert added.status_code == 201, added.text
    assert added.json()["data"] == {
        "id": added.json()["data"]["id"],
        "nickname": "小王",
        "is_placeholder": True,
    }
    rows = members(c, base)
    assert [(m["nickname"], m["is_placeholder"]) for m in rows[2:]] == [("小王", True)]
    assert [m["is_placeholder"] for m in rows[:2]] == [False, False]
    assert rows[2]["id"] == added.json()["data"]["id"]
    assert (rows[2]["is_owner"], rows[2]["is_current"]) == (False, False)
    trips = c.get("/v1/trips", headers=auth()).json()["data"]
    assert trips[0]["member_count"] == 3
    with SessionLocal() as session:
        row = session.scalar(select(TripMember).where(TripMember.is_placeholder))
        assert row.added_by == "b"
        assert session.get(User, row.user_id).wechat_openid_hash is None
    assert add(c, base, by="outsider").status_code == 404
    assert add(c, base, "   ").status_code == 422
    for name in ("二", "三", "四"):
        assert add(c, base, name).status_code == 201
    full = add(c, base, "七")
    assert full.status_code == 409
    assert full.json()["error"]["code"] == "TRIP_MEMBER_LIMIT_REACHED"
    assert len(members(c, base)) == 6


def test_placeholder_is_deleted_only_while_it_never_paid_owed_or_settled(ledger):
    c, base, p = placeholder_trip(ledger)
    # The app lists every member on an expense, also one it does not concern.
    saved, body = spend(c, base, {"a": "100", "b": "100"})
    body["payload"]["participants"] = ["a", "b", p]
    listed = c.patch(
        base + "/expenses/" + saved["id"], headers=auth(), json={**body, "revision": 1}
    )
    assert listed.status_code == 200, listed.text
    before = view(c, base)[0]

    assert (
        c.delete(base + "/placeholders/" + p, headers=auth("outsider")).status_code
        == 404
    )
    assert c.delete(base + "/placeholders/b", headers=auth()).status_code == 404
    removed = c.delete(base + "/placeholders/" + p, headers=auth("a"))

    assert removed.status_code == 204, removed.text
    assert [m["id"] for m in members(c, base)] == ["a", "b"]
    with SessionLocal() as session:
        assert session.get(User, p) is None
        record = session.get(ExpenseRecord, saved["id"])
        assert record.payload_json["participants"] == ["a", "b"]
        assert record.revision == 3
    after = view(c, base)[0]
    assert after["members"] == ["a", "b"]
    assert [d["balances"] for d in after["days"]] == [
        d["balances"] for d in before["days"]
    ]
    assert c.delete(base + "/placeholders/" + p, headers=auth()).status_code == 404


@pytest.mark.parametrize("use", ["share", "zero_share", "payer", "settled", "revoked"])
def test_placeholder_in_use_cannot_be_deleted(ledger, use):
    c, base, p = placeholder_trip(ledger)
    if use == "share":
        spend(c, base, {"a": "100", p: "100"})
    elif use == "zero_share":
        saved, body = spend(c, base, {"a": "100", "b": "100"})
        body["payload"]["participants"] = ["a", "b", p]
        body["payload"]["expenses"][0]["items"][0]["allocation"] = {"a": "1", p: "0"}
        assert (
            c.patch(
                base + "/expenses/" + saved["id"],
                headers=auth(),
                json={**body, "revision": 1},
            ).status_code
            == 200
        )
    elif use == "payer":
        spend(c, base, {"a": "100"}, payer=p)
    else:
        saved, _ = spend(c, base, {p: "100"}, payer="a")
        paid = mark(c, base, "a", "a") if False else confirm(c, base, p, "a")
        assert paid.status_code == 201, paid.text
        if use == "revoked":
            assert (
                c.post(
                    base + "/payments/" + paid.json()["data"]["id"] + "/revoke",
                    headers=auth(),
                ).status_code
                == 200
            )
            assert (
                c.delete(base + "/expenses/" + saved["id"], headers=auth()).status_code
                == 204
            )

    refused = c.delete(base + "/placeholders/" + p, headers=auth())

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "PLACEHOLDER_IN_USE"
    assert len(members(c, base)) == 3


def test_payer_marking_payment_to_a_placeholder_settles_it_at_once(ledger):
    c, base, p = placeholder_trip(ledger)
    spend(c, base, {"a": "100", "b": "100"}, payer=p)
    assert pairs(scope(c, base)["transfers"]) == [
        ("a", p, "100.00"),
        ("b", p, "100.00"),
    ]
    # Nobody else may speak for the payer, and nobody can confirm for p.
    assert mark(c, base, "a", p, by="b").status_code == 403
    assert confirm(c, base, "a", p, by="a").status_code == 403

    paid = mark(c, base, "a", p)

    assert paid.status_code == 201, paid.text
    record = paid.json()["data"]
    assert record["format_version"] == 2
    assert record["created_by"] == "a"
    assert record["on_behalf"] == {"by": "a", "placeholder": "recipient"}
    assert "marked_by" not in record
    data, days = view(c, base)
    assert data["payer_marks"] == []
    assert [(t["status"], *pairs([t])) for t in days[D1]["transfers"]] == [
        ("settled", ("a", p, "100.00")),
        ("pending", ("b", p, "100.00")),
    ]
    assert mark(c, base, "b", p).status_code == 201
    assert view(c, base)[1][D1]["status"] == "settled"
    with SessionLocal() as session:
        assert {r.to_id for r in session.scalars(select(PaymentRecord)).all()} == {p}


def test_recipient_confirms_directly_when_the_payer_is_a_placeholder(ledger):
    c, base, p = placeholder_trip(ledger)
    spend(c, base, {p: "90"}, payer="a")

    assert mark(c, base, p, "a", by="a").status_code == 403
    assert confirm(c, base, p, "a", by="b").status_code == 403
    paid = confirm(c, base, p, "a")

    assert paid.status_code == 201, paid.text
    assert paid.json()["data"]["on_behalf"] == {"by": "a", "placeholder": "payer"}
    assert view(c, base)[1][D1]["status"] == "settled"


@pytest.mark.parametrize("endpoint", ["mark", "confirm"])
def test_any_real_member_settles_between_two_placeholders(ledger, endpoint):
    c, base, p = placeholder_trip(ledger)
    q = add(c, base, "小李").json()["data"]["id"]
    spend(c, base, {q: "70"}, payer=p)
    act = mark if endpoint == "mark" else confirm

    assert act(c, base, q, p, by="outsider").status_code == 404
    paid = act(c, base, q, p, by="b")

    assert paid.status_code == 201, paid.text
    assert paid.json()["data"]["on_behalf"] == {"by": "b", "placeholder": "both"}
    assert paid.json()["data"]["created_by"] == "b"
    assert view(c, base)[1][D1]["status"] == "settled"


def test_transfers_between_real_members_are_untouched_by_a_placeholder(ledger):
    c, base, _ = placeholder_trip(ledger)
    spend(c, base, {"b": "80"}, payer="a")

    marked = mark(c, base, "b", "a")

    assert marked.status_code == 201
    assert marked.json()["data"]["marked_by"] == "b"
    assert "on_behalf" not in marked.json()["data"]
    paid = confirm(c, base, "b", "a", mark_id=marked.json()["data"]["id"])
    assert paid.status_code == 201
    assert "on_behalf" not in paid.json()["data"]


def test_invite_shows_placeholders_to_newcomers_only(ledger):
    c, base, p = placeholder_trip(ledger)
    token = invite(c, base)

    newcomer = c.get(f"/v1/trip-invites/{token}", headers=auth("r")).json()["data"]
    member = c.get(f"/v1/trip-invites/{token}", headers=auth("b")).json()["data"]

    assert newcomer["placeholders"] == [{"id": p, "nickname": "小王"}]
    assert (member["already_member"], member["placeholders"]) == (True, [])


def ledger_people(c, base, user, rename=None):
    """Each person's daily net and balance, and every settlement amount."""
    rename = rename or {}
    who = lambda m: rename.get(m, m)  # noqa: E731
    data, days = view(c, base, user)
    return {
        "members": sorted(who(m) for m in data["members"]),
        "days": {
            date: {
                "status": d["status"],
                "nets": sorted(
                    (g["currency"], who(m), v["amount"])
                    for g in d["groups"]
                    for m, v in g["net_by_participant"].items()
                ),
                "balances": sorted(
                    (b["currency"], who(b["participant_id"]), b["amount"])
                    for b in d["balances"]
                ),
                "pending": sorted(
                    (
                        who(t["from_participant_id"]),
                        who(t["to_participant_id"]),
                        t["amount"],
                    )
                    for t in d["pending"]
                ),
                "carries": len(d["carry_entries"]),
            }
            for date, d in days.items()
        },
        "payments": [
            (
                p["id"],
                p["currency"],
                p["amount"],
                who(p["from_participant_id"]),
                who(p["to_participant_id"]),
                p["created_by"],
                p.get("on_behalf"),
                bool(p.get("revoked_at")),
            )
            for p in data["payments"]
        ],
        "open": sorted(
            (who(t["from_participant_id"]), who(t["to_participant_id"]), t["amount"])
            for t in data["summary"]
        ),
    }


def test_claim_keeps_every_net_balance_and_settlement_amount(ledger):
    """Acceptance 1: three members, one a placeholder, with a settled transfer."""
    c, base, p = placeholder_trip(ledger)
    spend(c, base, {"a": "100", "b": "100", p: "100"}, payer="a", day=D1)
    spend(c, base, {"a": "60", p: "60"}, payer="b", day=D2)
    spend(c, base, {"b": "30"}, payer=p, day=D2)
    assert confirm(c, base, p, "a", [D1]).status_code == 201
    assert mark(c, base, "b", "a", [D1]).status_code == 201
    before = ledger_people(c, base, "a", {p: "r"})
    home_before = c.get("/v1/trips/ledger-summary", headers=auth("a")).json()["data"]
    with SessionLocal() as session:
        rows_before = [
            (r.id, r.amount, r.currency, r.created_by)
            for r in session.scalars(select(PaymentRecord).order_by(PaymentRecord.id))
        ]
        allocations_before = sorted(
            (a.payment_id, str(a.occurred_at), a.amount)
            for a in session.scalars(select(PaymentAllocationRecord))
        )
        revision = session.get(TripLedger, TRIP(base)).revision
    token = invite(c, base)

    claimed = claim(c, token, p, "r")

    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["data"]["member_count"] == 3
    assert ledger_people(c, base, "r") == before
    assert ledger_people(c, base, "a") == before
    rows = members(c, base, "r")
    assert [(m["id"], m["nickname"], m["is_placeholder"]) for m in rows] == [
        ("a", None, False),
        ("b", None, False),
        ("r", "小王", False),
    ]
    assert rows[2]["is_current"] is True
    home = c.get("/v1/trips/ledger-summary", headers=auth("a")).json()["data"]
    assert home["groups"] == home_before["groups"]
    with SessionLocal() as session:
        assert session.get(User, p) is None
        assert session.get(User, "r") is not None
        records = session.scalars(
            select(PaymentRecord).order_by(PaymentRecord.id)
        ).all()
        assert [
            (r.id, r.amount, r.currency, r.created_by) for r in records
        ] == rows_before
        assert [(r.from_id, r.to_id) for r in records] == [("r", "a")]
        assert records[0].payload_json["from_participant_id"] == "r"
        assert (
            sorted(
                (a.payment_id, str(a.occurred_at), a.amount)
                for a in session.scalars(select(PaymentAllocationRecord))
            )
            == allocations_before
        )
        ledger_row = session.get(TripLedger, TRIP(base))
        assert ledger_row.revision == revision + 1
        assert p not in str(ledger_row.state_json)
        for record in session.scalars(select(ExpenseRecord)):
            assert p not in str(record.payload_json)
            assert record.revision == 2
    # The open mark between the two others is still good, and r now acts alone.
    data, _ = view(c, base, "r")
    assert [m["stale"] for m in data["payer_marks"]] == [False]
    assert c.get("/v1/trips", headers=auth("r")).json()["data"][0]["id"] == TRIP(base)


def test_after_a_claim_transfers_need_both_sides_again(ledger):
    c, base, p = placeholder_trip(ledger)
    spend(c, base, {"a": "100", "b": "100"}, payer=p)
    assert mark(c, base, "a", p).status_code == 201
    assert claim(c, invite(c, base), p, "r").status_code == 200

    marked = mark(c, base, "b", "r")

    assert marked.status_code == 201
    assert marked.json()["data"]["marked_by"] == "b"
    assert "on_behalf" not in marked.json()["data"]
    assert view(c, base)[1][D1]["status"] == "pending"
    assert confirm(c, base, "b", "r", by="b").status_code == 403
    paid = confirm(c, base, "b", "r", mark_id=marked.json()["data"]["id"])
    assert paid.status_code == 201
    assert "on_behalf" not in paid.json()["data"]
    # The settlement made for the placeholder keeps its note and can be undone.
    data, _ = view(c, base, "r")
    first = data["payments"][0]
    assert first["on_behalf"] == {"by": "a", "placeholder": "recipient"}
    assert (first["from_participant_id"], first["to_participant_id"]) == ("a", "r")


def test_only_a_newcomer_with_a_live_link_claims_an_existing_placeholder(ledger):
    c, base, p = placeholder_trip(ledger)
    spend(c, base, {"a": "100", p: "100"})
    token = invite(c, base)
    before = ledger_people(c, base, "a")

    member = claim(c, token, p, "b")
    not_a_placeholder = claim(c, token, "a", "r")
    unknown = claim(c, token, "nobody", "r")
    bad_link = claim(c, "no-such-token", p, "r")

    # Acceptance 2: someone who is already a member is refused.
    assert member.status_code == 409
    assert member.json()["error"]["code"] == "ALREADY_A_MEMBER"
    assert not_a_placeholder.status_code == unknown.status_code == 409
    assert unknown.json()["error"]["code"] == "PLACEHOLDER_UNAVAILABLE"
    assert bad_link.status_code == 410
    assert ledger_people(c, base, "a") == before
    assert claim(c, token, p, "r").status_code == 200
    # Taken: a second person cannot claim it, but can still join as new.
    assert claim(c, token, p, "s").json()["error"]["code"] == "PLACEHOLDER_UNAVAILABLE"
    assert claim(c, token, p, "r").json()["error"]["code"] == "ALREADY_A_MEMBER"
    assert (
        c.post(f"/v1/trip-invites/{token}/accept", headers=auth("s")).status_code == 200
    )
    newer = invite(c, base)
    assert claim(c, token, p, "t").status_code == 410
    assert (
        c.get(f"/v1/trip-invites/{newer}", headers=auth("t")).json()["data"][
            "placeholders"
        ]
        == []
    )


def test_claim_that_fails_half_way_changes_nothing(ledger, monkeypatch):
    """Acceptance 3."""
    c, base, p = placeholder_trip(ledger)
    spend(c, base, {"a": "100", "b": "100", p: "100"})
    assert confirm(c, base, p, "a").status_code == 201
    token = invite(c, base)
    before = ledger_people(c, base, "a")

    def stored():
        with SessionLocal() as session:
            return (
                [
                    (m.user_id, m.is_placeholder)
                    for m in session.scalars(
                        select(TripMember).order_by(TripMember.user_id)
                    )
                ],
                [
                    (e.revision, e.payload_json)
                    for e in session.scalars(select(ExpenseRecord))
                ],
                [
                    (r.from_id, r.to_id, r.payload_json)
                    for r in session.scalars(select(PaymentRecord))
                ],
                session.get(TripLedger, TRIP(base)).state_json,
                session.get(TripLedger, TRIP(base)).revision,
                session.get(User, p) is not None,
                session.get(User, "r") is not None,
            )

    snapshot = stored()

    def fail(*_):
        raise RuntimeError("database went away")

    # Everything is rewritten by then; only deleting the user row is left.
    monkeypatch.setattr("app.api.v1.placeholders._delete_placeholder_user", fail)
    assert claim(c, token, p, "r").status_code == 500

    assert stored() == snapshot
    assert ledger_people(c, base, "a") == before
    monkeypatch.undo()
    assert claim(c, token, p, "r").status_code == 200


def test_full_trip_with_a_placeholder_can_still_be_claimed_but_not_joined(ledger):
    c, base, p = placeholder_trip(ledger)
    join(base, "c", "d", "e")
    assert len(members(c, base)) == 6

    token = c.post(base + "/invites", headers=auth())

    assert token.status_code == 201, token.text
    token = token.json()["data"]["token"]
    joined = c.post(f"/v1/trip-invites/{token}/accept", headers=auth("r"))
    assert joined.status_code == 409
    assert joined.json()["error"]["code"] == "TRIP_MEMBER_LIMIT_REACHED"
    claimed = claim(c, token, p, "r")
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["data"]["member_count"] == 6
    # No placeholder is left, so a full trip issues no further link.
    assert c.post(base + "/invites", headers=auth()).status_code == 409


@pytest.mark.parametrize("claimer", ["0-early", "zz-late"])
def test_claim_in_a_two_member_trip_keeps_the_settled_day_and_its_amounts(
    tmp_path, claimer
):
    """A lone member and a placeholder settle in the two-member format."""
    from app.db.session import configure_database, create_schema, drop_schema
    from app.main import app
    from fastapi.testclient import TestClient

    configure_database(f"sqlite+pysqlite:///{tmp_path / 'pair.db'}")
    create_schema()
    with TestClient(app) as c:
        trip = c.post(
            "/v1/trips",
            headers=auth("m"),
            json={"name": "独行", "default_currency": "CNY"},
        ).json()["data"]["id"]
        base = f"/v1/trips/{trip}"
        p = c.post(
            base + "/placeholders", headers=auth("m"), json={"nickname": "小王"}
        ).json()["data"]["id"]

        def record(amount, day):
            body = {
                "occurred_at": day,
                "payload": {
                    "title": "晚餐",
                    "participants": ["m", p],
                    "expenses": [
                        {
                            "expense_id": str(uuid4()),
                            "payer_id": "m",
                            "settlement_currency": "CNY",
                            "items": [
                                {
                                    "item_id": "i",
                                    "amount": {"currency": "CNY", "amount": amount},
                                    "allocation": {"m": "0.5", p: "0.5"},
                                }
                            ],
                        }
                    ],
                },
            }
            r = c.post(
                base + "/expenses",
                headers={**auth("m"), "Idempotency-Key": str(uuid4())},
                json=body,
            )
            assert r.status_code == 201, r.text
            return r.json()["data"], body

        saved, body = record("400", D1)
        record("100", D2)
        preview = c.post(
            base + "/payments/preview", headers=auth("m"), json={"dates": [D1]}
        ).json()["data"]
        paid = c.post(
            base + "/payments",
            headers=auth("m"),
            json={
                "dates": preview["dates"],
                "token": preview["token"],
                "currency": "CNY",
            },
        )
        assert paid.status_code == 201, paid.text
        assert "format_version" not in paid.json()["data"]
        assert paid.json()["data"]["on_behalf"] == {"by": "m", "placeholder": "payer"}
        before = ledger_people(c, base, "m", {p: claimer})
        token = c.post(base + "/invites", headers=auth("m")).json()["data"]["token"]

        assert claim(c, token, p, claimer).status_code == 200

        assert ledger_people(c, base, "m") == before
        assert ledger_people(c, base, claimer) == before
        with SessionLocal() as session:
            row = session.scalars(select(PaymentRecord)).one()
            assert (row.from_id, row.to_id, str(row.amount)) == (claimer, "m", "200.00")
            flips = (p < "m") != (claimer < "m")
            allocation = session.scalars(select(PaymentAllocationRecord)).one()
            stored = paid.json()["data"]["allocations"][0]["amount"]
            assert str(allocation.amount) == (
                str(-__import__("decimal").Decimal(stored)) if flips else stored
            )
        # Editing the settled day still rolls exactly the difference forward.
        body["payload"]["participants"] = ["m", claimer]
        bill = body["payload"]["expenses"][0]
        bill["items"][0]["amount"]["amount"] = "460"
        bill["items"][0]["allocation"] = {"m": "0.5", claimer: "0.5"}
        edited = c.patch(
            base + "/expenses/" + saved["id"],
            headers=auth("m"),
            json={**body, "revision": 2},
        )
        assert edited.status_code == 200, edited.text
        after = ledger_people(c, base, "m")
        assert after["days"][D1]["status"] == "settled"
        assert after["open"] == [(claimer, "m", "80.00")]
        # And the settlement can still be revoked afterwards.
        assert (
            c.post(
                base + "/payments/" + paid.json()["data"]["id"] + "/revoke",
                headers=auth(claimer),
            ).status_code
            == 200
        )
    drop_schema()
