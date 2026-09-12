import asyncio
from datetime import date

import httpx
import pytest
from app.providers.rates import FrankfurterRates, RateUnavailable
from app.providers.receipt_ocr import parse_text_lines


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


def test_receipt_candidate_parser_does_not_treat_total_or_tax_as_product():
    result = parse_text_lines(["お茶 120", "牛乳 230", "消費税 28", "合計 378"], "JPY")
    assert [x["source_text"] for x in result["items"]] == ["お茶", "牛乳"]
    assert result["total_candidate"] == "378"
    assert result["tax_candidate"] == "28"
    assert result["requires_review"] is True
