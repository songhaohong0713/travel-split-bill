import asyncio

import pytest
from app.providers.cloudbase_receipt_ai import (
    CloudBaseReceiptAi,
    CloudBaseReceiptAiError,
)


class FakeResponse:
    status_code = 200

    def __init__(self, content: str) -> None:
        self._content = content

    def json(self) -> dict[str, object]:
        return {"choices": [{"message": {"content": self._content}}]}


class FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

    async def post(self, _: str, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        return FakeResponse(self.content)


def test_recognize_returns_only_complete_receipt_item_candidates() -> None:
    client = FakeClient(
        '{"items":[{"source_text":"お茶","translated_text":"茶","amount":"120","currency":"JPY"},'
        '{"source_text":"bad","translated_text":"坏","amount":"x","currency":"JPY"}]}'
    )
    provider = CloudBaseReceiptAi("env-id", "api-key", client=client)

    candidates = asyncio.run(provider.recognize(b"jpeg"))

    assert candidates == [
        {
            "source_text": "お茶",
            "translated_text": "茶",
            "amount": "120",
            "currency": "JPY",
        }
    ]
    assert client.calls[0]["headers"] == {"Authorization": "Bearer api-key"}
    payload = client.calls[0]["json"]
    assert isinstance(payload, dict)
    assert payload["model"] == "glm-5v-turbo"


def test_recognize_rejects_non_json_or_empty_items() -> None:
    provider = CloudBaseReceiptAi("env-id", "api-key", client=FakeClient("not json"))

    with pytest.raises(CloudBaseReceiptAiError):
        asyncio.run(provider.recognize(b"jpeg"))
