"""Phase 3: trips of up to six members joined through one invite link."""

from datetime import timedelta

from app.api.v1.trip_invites import _hash, _now
from app.db.models import TripInvite
from app.db.session import SessionLocal
from test_ledger import auth, expense, pay, preview
from test_ledger import ledger as ledger  # noqa: PLC0414
from test_trip_collaboration import client as client  # noqa: PLC0414
from test_trip_collaboration import create_trip

JOINERS = ["peer-b", "peer-c", "peer-d", "peer-e", "peer-f"]


def invite(client, trip_id, user="owner-a"):
    return client.post(f"/v1/trips/{trip_id}/invites", headers=auth(user))


def accept(client, token, user):
    return client.post(f"/v1/trip-invites/{token}/accept", headers=auth(user))


def test_one_link_admits_five_people_and_refuses_the_sixth(client):
    trip_id = create_trip(client)
    token = invite(client, trip_id).json()["data"]["token"]

    counts = []
    for user in JOINERS:
        joined = accept(client, token, user)
        assert joined.status_code == 200, joined.text
        counts.append(joined.json()["data"]["member_count"])

    assert counts == [2, 3, 4, 5, 6]
    members = client.get(f"/v1/trips/{trip_id}/members", headers=auth("peer-f"))
    assert len(members.json()["data"]) == 6
    refused = accept(client, token, "late-g")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "TRIP_MEMBER_LIMIT_REACHED"
    assert "6" in refused.json()["error"]["message"]
    assert (
        client.get(f"/v1/trips/{trip_id}/expenses", headers=auth("late-g")).status_code
        == 404
    )
    # A full trip cannot issue another link either.
    assert invite(client, trip_id).status_code == 409


def test_opening_the_link_again_says_already_joined(client):
    trip_id = create_trip(client)
    token = invite(client, trip_id).json()["data"]["token"]
    assert accept(client, token, "peer-b").status_code == 200
    assert accept(client, token, "peer-c").status_code == 200

    for user in ("peer-b", "owner-a"):
        again = accept(client, token, user)
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "ALREADY_A_MEMBER"
    shown = client.get(f"/v1/trip-invites/{token}", headers=auth("peer-b"))
    assert shown.json()["data"]["already_member"] is True
    trips = client.get("/v1/trips", headers=auth("peer-b")).json()["data"]
    assert trips[0]["member_count"] == 3


def test_new_link_replaces_the_old_one_and_only_the_creator_issues_links(client):
    trip_id = create_trip(client)
    old = invite(client, trip_id).json()["data"]["token"]
    assert accept(client, old, "peer-b").status_code == 200
    assert invite(client, trip_id, "peer-b").status_code == 404

    new = invite(client, trip_id).json()["data"]["token"]

    assert accept(client, old, "peer-c").status_code == 410
    assert (
        client.get(f"/v1/trip-invites/{old}", headers=auth("peer-c")).status_code == 410
    )
    assert accept(client, new, "peer-c").status_code == 200


def test_expired_and_formerly_single_use_links_stay_dead(client):
    trip_id = create_trip(client)
    used = invite(client, trip_id).json()["data"]["token"]
    with SessionLocal() as session:
        row = session.query(TripInvite).filter_by(token_hash=_hash(used)).one()
        row.used_at = _now()
        session.commit()
    assert accept(client, used, "peer-b").status_code == 410

    late = invite(client, trip_id).json()["data"]["token"]
    with SessionLocal() as session:
        row = session.query(TripInvite).filter_by(token_hash=_hash(late)).one()
        row.expires_at = _now() - timedelta(seconds=1)
        session.commit()
    assert accept(client, late, "peer-b").status_code == 410


def test_new_member_leaves_existing_expenses_and_settlements_untouched(ledger):
    c, base = ledger
    expense(c, base, "200", "2026-10-05")
    paid = pay(c, base, preview(c, base, ["2026-10-05"])).json()["data"]
    expense(c, base, "400", "2026-10-06", payer="b")
    before = c.get(base + "/ledger", headers=auth()).json()["data"]
    summary_before = c.get("/v1/trips/ledger-summary", headers=auth()).json()["data"]
    token = c.post(base + "/invites", headers=auth()).json()["data"]["token"]

    joined = c.post(f"/v1/trip-invites/{token}/accept", headers=auth("c"))

    assert joined.json()["data"]["member_count"] == 3
    after = c.get(base + "/ledger", headers=auth()).json()["data"]
    assert after["members"] == ["a", "b", "c"]
    assert after["payments"] == before["payments"] == [paid]
    for old, new in zip(before["days"], after["days"], strict=True):
        assert (new["date"], new["status"]) == (old["date"], old["status"])
        assert new["balances"] == old["balances"]
        assert new["pending"] == [
            {k: v for k, v in p.items() if k != "signed_amount"} for p in old["pending"]
        ]
        assert new["carry_entries"] == old["carry_entries"] == []
        for old_group, new_group in zip(old["groups"], new["groups"], strict=True):
            nets = new_group["net_by_participant"]
            assert nets["c"]["amount"] == "0.00"
            assert {m: nets[m] for m in "ab"} == old_group["net_by_participant"]
    assert [
        (t["from_participant_id"], t["to_participant_id"], t["amount"])
        for t in after["summary"]
    ] == [("a", "b", "200.00")]
    assert before["summary"][0]["amount"] == "200.00"
    # The newcomer owes and is owed nothing; the others' totals did not move.
    newcomer = c.get("/v1/trips/ledger-summary", headers=auth("c")).json()["data"]
    assert newcomer["groups"] == []
    assert newcomer["trips"][0]["pending"] == []
    assert (
        c.get("/v1/trips/ledger-summary", headers=auth()).json()["data"]["groups"]
        == summary_before["groups"]
    )
    # The settled day stays revocable and the open one can still be settled.
    scope = c.post(base + "/payments/preview", headers=auth("b"), json={}).json()[
        "data"
    ]
    settled = c.post(
        base + "/payments",
        headers=auth("b"),
        json={
            "dates": scope["dates"],
            "token": scope["token"],
            "currency": "CNY",
            "from_participant_id": "a",
            "to_participant_id": "b",
        },
    )
    assert settled.status_code == 201, settled.text
    days = c.get(base + "/ledger", headers=auth("c")).json()["data"]["days"]
    assert {d["status"] for d in days} == {"settled"}
