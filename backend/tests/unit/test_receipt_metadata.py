import json

from app.providers.deepseek_receipt_ai import parse_receipt_candidates


def test_receipt_metadata_is_validated_and_preserved():
    raw = {
        "merchant": "药妆店",
        "occurred_at": "2026-10-07",
        "items": [
            {
                "source_text": "薬",
                "translated_text": "药",
                "amount": "1280",
                "currency": "JPY",
            }
        ],
    }
    result = parse_receipt_candidates(json.dumps(raw))
    assert result[0]["merchant"] == "药妆店"
    assert result[0]["occurred_at"] == "2026-10-07"
    raw["occurred_at"] = "2026-02-30"
    assert "occurred_at" not in parse_receipt_candidates(json.dumps(raw))[0]
