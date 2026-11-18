import asyncio
from datetime import UTC, date, datetime

import httpx
import pytest
from app.main import app, configure_receipt_ai, configure_wechat_auth
from app.providers.deepseek_receipt_ai import DeepSeekReceiptAi
from app.providers.rates import FrankfurterRates, RateUnavailable
from app.providers.receipt_ocr import parse_text_lines
from app.providers.wechat_auth import WechatAuthError, WechatCode2Session


def test_historical_rate_keeps_decimal_and_effective_date():
    def respond(request):
        assert request.url.params["date"] == "2026-08-15"
        return httpx.Response(
            200,
            text='{"date":"2026-08-14","base":"JPY","quote":"CNY","rate":0.047123456789123456}',
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    result = asyncio.run(
        FrankfurterRates(client).quote(date(2026, 8, 15), "JPY", "CNY")
    )
    assert result["rate"] == "0.047123456789123456"
    assert result["effective_date"] == "2026-08-14"
    assert result["requested_date"] == "2026-08-15"


def test_rate_failure_is_explicit():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    with pytest.raises(RateUnavailable):
        asyncio.run(FrankfurterRates(client).quote(date(2026, 8, 15), "JPY", "CNY"))


def test_local_today_after_midnight_uses_latest_published_utc_quote(monkeypatch):
    from app.core.ledger import client_timezone_offset

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 29, 15, 30, tzinfo=UTC)

    monkeypatch.setattr('app.providers.rates.datetime', Clock)
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.params['date'] == '2026-10-29'
        return httpx.Response(200, json={'date': '2026-10-29', 'base': 'JPY', 'quote': 'CNY', 'rate': '0.042188'})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    token = client_timezone_offset.set(-540)
    try:
        result = asyncio.run(FrankfurterRates(client).quote(date(2026, 10, 30), 'JPY', 'CNY'))
        assert result['requested_date'] == '2026-10-30'
        assert result['effective_date'] == '2026-10-29'
        with pytest.raises(RateUnavailable, match='未来'):
            asyncio.run(FrankfurterRates(client).quote(date(2026, 10, 31), 'JPY', 'CNY'))
        assert len(requests) == 1
    finally:
        client_timezone_offset.reset(token)


def test_rate_future_guard_uses_local_date_west_of_utc(monkeypatch):
    from app.core.ledger import client_timezone_offset

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 30, 1, 30, tzinfo=UTC)

    monkeypatch.setattr('app.providers.rates.datetime', Clock)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail('future quote must not call upstream')))
    token = client_timezone_offset.set(420)
    try:
        with pytest.raises(RateUnavailable, match='未来'):
            asyncio.run(FrankfurterRates(client).quote(date(2026, 10, 30), 'JPY', 'CNY'))
    finally:
        client_timezone_offset.reset(token)


def test_receipt_candidate_parser_does_not_treat_total_or_tax_as_product():
    result = parse_text_lines(["お茶 120", "牛乳 230", "消費税 28", "合計 378"], "JPY")
    assert [x["source_text"] for x in result["items"]] == ["お茶", "牛乳"]
    assert result["total_candidate"] == "378"
    assert result["tax_candidate"] == "28"
    assert result["requires_review"] is True

def test_configure_wechat_auth_uses_cloudbase_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WECHAT_APP_ID", "wx-test-app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "test-app-secret")
    if hasattr(app.state, "wechat_auth"):
        delattr(app.state, "wechat_auth")

    configure_wechat_auth()

    provider = app.state.wechat_auth
    assert isinstance(provider, WechatCode2Session)
    assert provider.app_id == "wx-test-app-id"
    assert provider.app_secret == "test-app-secret"


def test_configure_receipt_ai_uses_deepseek_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "receipt-model")
    if hasattr(app.state, "receipt_ai"):
        delattr(app.state, "receipt_ai")

    configure_receipt_ai()

    provider = app.state.receipt_ai
    assert isinstance(provider, DeepSeekReceiptAi)
    assert provider.model == "receipt-model"

def test_wechat_provider_reports_wechat_error_code():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"errcode": 40125, "errmsg": "invalid app secret"})
        )
    )
    provider = WechatCode2Session("wx-test", "secret", client)

    with pytest.raises(WechatAuthError, match="40125"):
        asyncio.run(provider.openid_for_code("temporary-code"))
