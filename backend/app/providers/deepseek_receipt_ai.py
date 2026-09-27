"""DeepSeek multimodal receipt extraction with strict output validation."""

import base64
import json
import logging
import os
import re
from typing import Any

import httpx
from app.core.money import decimal_from_string

logger = logging.getLogger(__name__)


class DeepSeekReceiptAiError(RuntimeError):
    pass


class DeepSeekReceiptAi:
    url = "https://api.deepseek.com/v1/chat/completions"

    def __init__(
        self, api_key: str, model: str = "deepseek-flash", client: httpx.AsyncClient | None = None
    ) -> None:
        self.api_key = api_key
        self.model = model
        self._client = client

    @classmethod
    def from_environment(cls) -> "DeepSeekReceiptAi":
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise DeepSeekReceiptAiError("DeepSeek API key is not configured")
        return cls(api_key, os.getenv("DEEPSEEK_MODEL", "deepseek-flash"))

    async def recognize(self, image: bytes) -> list[dict[str, str]]:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Read this Japanese or English receipt. Translate item names into Chinese. "
                                "Return only JSON in this exact shape: "
                                '{"items":[{"source_text":"","translated_text":"","amount":"","currency":""}]}. '
                                "Include only purchased item lines. amount must be the positive line total used for "
                                "settlement; when quantity and unit price are present, calculate their line total. "
                                "Exclude receipt totals, tax, service fees, discounts, refunds, and payment rows. "
                                "currency must be ISO 4217."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")
                            },
                        },
                    ],
                }
            ],
        }
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30.0)
        try:
            response = await client.post(self.url, headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
        except httpx.HTTPError as exc:
            raise DeepSeekReceiptAiError("DeepSeek request failed") from exc
        finally:
            if owns_client:
                await client.aclose()
        if response.status_code != 200:
            logger.warning("deepseek_receipt_request_rejected status=%s", response.status_code)
            raise DeepSeekReceiptAiError("DeepSeek rejected receipt extraction")
        try:
            message = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise DeepSeekReceiptAiError("DeepSeek returned an invalid response") from exc
        return parse_receipt_candidates(message)


def parse_receipt_candidates(content: Any) -> list[dict[str, str]]:
    if not isinstance(content, str):
        raise DeepSeekReceiptAiError("DeepSeek returned non-text content")
    try:
        items = json.loads(content).get("items")
    except (AttributeError, json.JSONDecodeError) as exc:
        raise DeepSeekReceiptAiError("DeepSeek returned invalid receipt JSON") from exc
    if not isinstance(items, list):
        raise DeepSeekReceiptAiError("DeepSeek returned invalid receipt items")
    candidates: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source_text") or "").strip()
        translated = str(item.get("translated_text") or "").strip()
        amount = str(item.get("amount") or "").strip()
        currency = str(item.get("currency") or "").strip().upper()
        if not source or not translated or not re.fullmatch(r"[A-Z]{3}", currency):
            continue
        try:
            if decimal_from_string(amount) <= 0:
                continue
        except (ValueError, ArithmeticError):
            continue
        candidates.append(
            {"source_text": source, "translated_text": translated, "amount": amount, "currency": currency}
        )
    if not candidates:
        raise DeepSeekReceiptAiError("DeepSeek found no valid receipt items")
    return candidates
