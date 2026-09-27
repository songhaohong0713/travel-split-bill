"""CloudBase multimodal receipt extraction with deliberately strict output."""

import base64
import json
import os
import re
from typing import Any

import httpx
from app.core.money import decimal_from_string


class CloudBaseReceiptAiError(RuntimeError):
    pass


class CloudBaseReceiptAi:
    def __init__(self, env_id: str, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self.url = f"https://{env_id}.api.tcloudbasegateway.com/v1/ai/cloudbase/chat/completions"
        self.api_key = api_key
        self._client = client

    @classmethod
    def from_environment(cls) -> "CloudBaseReceiptAi":
        env_id = os.getenv("CLOUDBASE_ENV_ID")
        api_key = os.getenv("CLOUDBASE_APIKEY") or os.getenv("CLOUDBASE_API_KEY")
        if not env_id or not api_key:
            raise CloudBaseReceiptAiError("CloudBase AI credentials are not configured")
        return cls(env_id, api_key)

    async def recognize(self, image: bytes) -> list[dict[str, str]]:
        payload = {
            "model": "glm-5v-turbo",
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
                                "Include only purchased item lines; amount must be positive and currency must be ISO 4217."
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
            raise CloudBaseReceiptAiError("CloudBase AI request failed") from exc
        finally:
            if owns_client:
                await client.aclose()
        if response.status_code != 200:
            raise CloudBaseReceiptAiError("CloudBase AI rejected receipt extraction")
        try:
            message = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise CloudBaseReceiptAiError("CloudBase AI returned an invalid response") from exc
        return parse_receipt_candidates(message)


def parse_receipt_candidates(content: Any) -> list[dict[str, str]]:
    if not isinstance(content, str):
        raise CloudBaseReceiptAiError("CloudBase AI returned non-text content")
    try:
        items = json.loads(content).get("items")
    except (AttributeError, json.JSONDecodeError) as exc:
        raise CloudBaseReceiptAiError("CloudBase AI returned invalid receipt JSON") from exc
    if not isinstance(items, list):
        raise CloudBaseReceiptAiError("CloudBase AI returned invalid receipt items")
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
        candidates.append({"source_text": source, "translated_text": translated, "amount": amount, "currency": currency})
    if not candidates:
        raise CloudBaseReceiptAiError("CloudBase AI found no valid receipt items")
    return candidates
