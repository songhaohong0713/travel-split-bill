"""Historical reference quotes; no receipt or identity data leaves this provider."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
from app.core.money import currency_scale


class RateUnavailable(ValueError):
    pass


class FrankfurterRates:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def quote(self, day: date, source: str, target: str) -> dict[str, str]:
        currency_scale(source)
        currency_scale(target)
        if day > datetime.now(UTC).date():
            raise RateUnavailable("不能查询未来的汇率")
        if source == target:
            return {
                "rate": "1",
                "requested_date": day.isoformat(),
                "effective_date": day.isoformat(),
                "from_currency": source,
                "to_currency": target,
                "provider": "same-currency",
            }
        try:
            response = await self.client.get(
                f"https://api.frankfurter.dev/v2/rate/{source}/{target}",
                params={"date": day.isoformat()},
                timeout=10,
            )
            response.raise_for_status()
            # Parsing directly to Decimal prevents JSON's default binary float conversion.
            data = json.loads(response.text, parse_float=Decimal)
            rate = Decimal(str(data["rate"]))
            effective = date.fromisoformat(data["date"])
            if not rate.is_finite() or rate <= 0 or effective > day:
                raise ValueError("invalid quote")
            if data.get("base") != source or data.get("quote") != target:
                raise ValueError("wrong currency pair")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise RateUnavailable("历史汇率暂不可用，请填写汇率或稍后重试") from exc
        return {
            "rate": format(rate, "f"),
            "requested_date": day.isoformat(),
            "effective_date": effective.isoformat(),
            "from_currency": source,
            "to_currency": target,
            "provider": "frankfurter",
        }
