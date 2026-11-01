from datetime import UTC, datetime

from test_ledger import (
    auth,
    expense,
    pay,
    preview,
)
from test_ledger import ledger as ledger  # noqa: PLC0414


def test_jpy_trip_keeps_jpy_after_create_save_read_and_settlement_preview(ledger):
    c, _ = ledger
    trip = c.post('/v1/trips', headers=auth(), json={'name': '日元结算回归', 'default_currency': 'JPY'}).json()['data']
    base = '/v1/trips/' + trip['id']
    token = c.post(base + '/invites', headers=auth()).json()['data']['token']
    assert c.post('/v1/trip-invites/' + token + '/accept', headers=auth('b')).status_code == 200
    saved, _ = expense(c, base, amount='1234', currency='JPY')
    records = c.get(base + '/expenses', headers=auth()).json()['data']
    assert next(r for r in records if r['id'] == saved['id'])['payload']['expenses'][0]['settlement_currency'] == 'JPY'
    view = c.get(base + '/ledger', headers=auth()).json()['data']
    assert [g['currency'] for g in view['days'][0]['groups']] == ['JPY']
    p = preview(c, base)
    assert [(g['currency'], g['amount']) for g in p['groups']] == [('JPY', '617')]


def test_renaming_a_settled_legacy_item_does_not_change_money_or_generate_carry(ledger):
    c, base = ledger
    saved, body = expense(c, base, amount='3.00', currency='JPY')
    bill = body['payload']['expenses'][0]
    bill.update(settlement_currency='CNY', reference_rate='1')
    assert c.patch(base + '/expenses/' + saved['id'], headers=auth(), json={**body, 'revision': 1}).status_code == 200
    p = preview(c, base)
    assert p['groups'][0]['amount'] == '1.50'
    assert pay(c, base, p).status_code == 201
    before = c.get(base + '/ledger', headers=auth()).json()['data']
    bill['items'][0]['name'] = '只改商品名称'
    body['payload']['title'] = '只改商户标题'
    response = c.patch(base + '/expenses/' + saved['id'], headers=auth(), json={**body, 'revision': 2})
    assert response.status_code == 200, response.text
    after = c.get(base + '/ledger', headers=auth()).json()['data']
    assert after['days'][0]['groups'] == before['days'][0]['groups']
    assert after['summary'] == []
    assert len(after['days']) == 1
    assert after['days'][0]['status'] == 'settled'
    assert not after['days'][0]['carry_entries']


def test_combined_mark_cannot_be_used_for_a_different_subset(ledger):
    c, base = ledger
    expense(c, base, amount='20', day='2026-10-07')
    expense(c, base, amount='20', day='2026-10-08')
    combined = preview(c, base)
    marked = c.post(base + '/payments/mark-paid', headers=auth('b'), json={'dates': combined['dates'], 'token': combined['token'], 'currency': 'CNY'})
    assert marked.status_code == 201
    single = preview(c, base, ['2026-10-07'])
    body = {'dates': single['dates'], 'token': single['token'], 'currency': 'CNY'}
    response = c.post(base + '/payments', headers=auth(), json={**body, 'mark_id': marked.json()['data']['id']})
    assert response.status_code == 409
    assert c.get(base + '/ledger', headers=auth()).json()['data']['payments'] == []
    assert c.post(base + '/payments', headers=auth(), json=body).status_code == 201
    after = c.get(base + '/ledger', headers=auth()).json()['data']
    assert {d['date']: d['status'] for d in after['days']} == {'2026-10-07': 'settled', '2026-10-08': 'pending'}
    assert [a['date'] for a in after['payments'][0]['allocations']] == ['2026-10-07']
    assert after['summary'][0]['amount'] == '10.00'


def test_editing_the_final_settled_trip_day_creates_payable_carry_after_trip_end(ledger):
    c, base = ledger
    assert c.patch(base, headers=auth(), json={'start_date': '2026-10-07', 'end_date': '2026-10-07'}).status_code == 200
    saved, body = expense(c, base, amount='400', day='2026-10-07')
    assert pay(c, base, preview(c, base)).status_code == 201
    body['payload']['expenses'][0]['items'][0]['amount']['amount'] = '460'
    response = c.patch(base + '/expenses/' + saved['id'], headers=auth(), json={**body, 'revision': 1})
    assert response.status_code == 200, response.text
    after = c.get(base + '/ledger', headers=auth()).json()['data']
    days = {d['date']: d for d in after['days']}
    assert days['2026-10-07']['status'] == 'settled'
    assert days['2026-10-08']['status'] == 'pending'
    assert days['2026-10-08']['carry_entries'][0]['source_date'] == '2026-10-07'
    p = preview(c, base, ['2026-10-08'])
    assert p['groups'][0]['amount'] == '30.00'


def test_payer_mark_waits_for_recipient_and_rejects_stale_mark(ledger):
    c, base = ledger
    expense(c, base)
    p = preview(c, base)
    body = {"dates": p["dates"], "token": p["token"], "currency": "CNY"}
    assert c.post(base + "/payments", headers=auth("b"), json=body).status_code == 403
    assert (
        c.post(base + "/payments/mark-paid", headers=auth("a"), json=body).status_code
        == 403
    )
    marked = c.post(base + "/payments/mark-paid", headers=auth("b"), json=body)
    assert marked.status_code == 201, marked.text
    mark = marked.json()["data"]
    result = c.get(base + "/ledger", headers=auth()).json()["data"]
    assert result["days"][0]["status"] == "pending"
    assert result["payer_marks"][0]["id"] == mark["id"]
    assert result["payments"] == []
    assert (
        c.post(
            base + "/payments", headers=auth(), json={**body, "mark_id": mark["id"]}
        ).status_code
        == 409
    )
    p = preview(c, base)
    confirmed = c.post(
        base + "/payments",
        headers=auth(),
        json={**body, "token": p["token"], "mark_id": mark["id"]},
    )
    assert confirmed.status_code == 201, confirmed.text
    result = c.get(base + "/ledger", headers=auth()).json()["data"]
    assert result["days"][0]["status"] == "settled"
    assert (
        result["payer_marks"][0]["confirmed_payment_id"]
        == confirmed.json()["data"]["id"]
    )


def test_today_cannot_close_or_settle(ledger):
    c, base = ledger
    today = datetime.now(UTC).date().isoformat()
    expense(c, base, day=today)
    assert c.post(base + "/days/" + today + "/close", headers=auth()).status_code == 422
    assert (
        c.post(
            base + "/payments/preview", headers=auth(), json={"dates": [today]}
        ).status_code
        == 422
    )


def test_offline_trip_uuid_replay_is_owner_and_payload_scoped(ledger):
    from uuid import uuid4

    c, _ = ledger
    body = {"name": "离线旅行", "default_currency": "CNY", "client_id": str(uuid4())}
    first = c.post("/v1/trips", headers=auth(), json=body)
    assert first.status_code == 201
    assert first.json()["data"]["id"] == body["client_id"]
    assert c.post("/v1/trips", headers=auth(), json=body).json() == first.json()
    assert (
        c.post("/v1/trips", headers=auth(), json={**body, "name": "不同"}).status_code
        == 409
    )
    assert c.post("/v1/trips", headers=auth("b"), json=body).status_code == 409


def test_server_expense_summary_uses_saved_algorithm(ledger):
    c, base = ledger
    saved, _ = expense(c, base)
    data = c.get(base + "/ledger", headers=auth()).json()["data"]
    summary = data["expense_summaries"][0]
    assert summary["id"] == saved["id"]
    assert summary["groups"][0]["paid_by_participant"]["a"]["amount"] == "400.00"
    assert data["totals"][0] == {"currency": "CNY", "amount": "400.00"}


def test_mark_is_revocable_and_cannot_confirm_after_expense_change(ledger):
    c, base = ledger
    saved, body = expense(c, base)
    p = preview(c, base)
    mark = c.post(
        base + "/payments/mark-paid",
        headers=auth("b"),
        json={"dates": p["dates"], "token": p["token"], "currency": "CNY"},
    ).json()["data"]
    assert (
        c.post(
            base + "/payer-marks/" + mark["id"] + "/revoke", headers=auth()
        ).status_code
        == 403
    )
    body["payload"]["expenses"][0]["items"][0]["amount"]["amount"] = "420"
    assert (
        c.patch(
            base + "/expenses/" + saved["id"],
            headers=auth(),
            json={**body, "revision": 1},
        ).status_code
        == 200
    )
    p = preview(c, base)
    assert (
        c.post(
            base + "/payments",
            headers=auth(),
            json={
                "dates": p["dates"],
                "token": p["token"],
                "currency": "CNY",
                "mark_id": mark["id"],
            },
        ).status_code
        == 409
    )
    result = c.get(base + "/ledger", headers=auth()).json()["data"]
    assert result["payer_marks"][0]["stale"] is True
    assert (
        c.post(
            base + "/payer-marks/" + mark["id"] + "/revoke", headers=auth("b")
        ).status_code
        == 200
    )
    assert not c.get(base + "/ledger", headers=auth()).json()["data"]["payments"]

def test_v2_summary_matches_past_days_and_excludes_today(ledger):
    c, base = ledger
    expense(c, base, day='2026-09-29')
    expense(c, base, day='2026-09-30')
    today = datetime.now(UTC).date().isoformat()
    expense(c, base, day=today)
    detail = c.get(base + '/ledger', headers=auth('b')).json()['data']
    summary = c.get('/v1/trips/ledger-summary', headers=auth('b')).json()['data']
    assert detail['contract_version'] == summary['contract_version'] == 2
    assert [d['status'] for d in detail['days'] if d['date'] < today] == ['pending','pending']
    assert next(d for d in detail['days'] if d['date'] == today)['status'] == 'recording'
    trip = next(t for t in summary['trips'] if t['id'] == base.split('/')[-1])
    assert trip['expense_count'] == 3
    assert trip['day_count'] == 3
    assert trip['has_recording']
    assert trip['pending'][0]['amount'] == detail['summary'][0]['amount']
    assert trip['pending'][0]['direction'] == 'payable'
