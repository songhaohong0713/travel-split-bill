"""Conservative OCR candidates. Every result must be reviewed before settlement."""

import base64
import re
from typing import Any

from app.core.money import decimal_from_string

_PRICE = re.compile(r"^(.*?)\s+[¥￥₩$€]?\s*(-?[\d,]+(?:\.\d+)?)\s*$")
_TOTAL = re.compile(r"合計|総計|total|합계", re.IGNORECASE)
_TAX = re.compile(r"消費税|内税|外税|tax|부가세", re.IGNORECASE)
_OTHER = re.compile(
    r"小計|subtotal|お預|お釣|change|cash|card|釣銭|割引|discount", re.IGNORECASE
)


def parse_text_lines(lines: list[str], currency: str) -> dict[str, Any]:
    items: list[dict[str, str]] = []
    result: dict[str, Any] = {
        "items": items,
        "raw_lines": lines,
        "currency_candidate": currency,
        "total_candidate": None,
        "tax_candidate": None,
        "requires_review": True,
        "warnings": ["商品・税額・合計を原票と照合してください"],
    }
    for text in lines:
        match = _PRICE.match(text.strip())
        if match is None:
            continue
        name, amount = match.groups()
        amount = amount.replace(",", "")
        decimal_from_string(amount)
        if _TAX.search(name):
            result["tax_candidate"] = amount
        elif _OTHER.search(name):
            continue
        elif _TOTAL.search(name):
            result["total_candidate"] = amount
        elif name.strip():
            items.append(
                {
                    "source_text": name.strip(),
                    "translated_text": "",
                    "amount": amount,
                    "currency": currency,
                }
            )
    result["warnings"] = ["请核对商品、税额和合计；识别候选不会直接参与结算"]
    return result


class TencentReceiptOcr:
    """SDK client is injected so credentials stay in deployment configuration."""

    def __init__(self, client: Any):
        self.client = client

    def recognize(self, image: bytes, language: str = "auto") -> list[str]:
        from tencentcloud.ocr.v20181119 import models  # type: ignore[import-untyped]

        request = models.GeneralBasicOCRRequest()
        request.ImageBase64 = base64.b64encode(image).decode("ascii")
        request.LanguageType = {"ja": "jap", "ko": "kor", "en": "auto"}.get(
            language, "auto"
        )
        response = self.client.GeneralBasicOCR(request)
        return [entry.DetectedText for entry in response.TextDetections]
