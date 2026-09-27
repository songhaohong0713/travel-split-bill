import asyncio

import pytest

from app.providers.deepseek_receipt_ai import DeepSeekReceiptAi, DeepSeekReceiptAiError


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


def test_recognize_sends_jpeg_to_deepseek_vision_endpoint() -> None:
    client = FakeClient(
        '{"items":[{"source_text":"お茶","translated_text":"茶","amount":"120","currency":"JPY"}]}'
    )
    provider = DeepSeekReceiptAi("deepseek-key", client=client)

    candidates = asyncio.run(provider.recognize(b"jpeg"))

    assert candidates == [
        {
            "source_text": "お茶",
            "translated_text": "茶",
            "amount": "120",
            "currency": "JPY",
        }
    ]
    assert client.calls[0]["headers"] == {"Authorization": "Bearer deepseek-key"}
    payload = client.calls[0]["json"]
    assert isinstance(payload, dict)
    assert payload["model"] == "deepseek-flash"
    content = payload["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_from_environment_requires_deepseek_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(DeepSeekReceiptAiError):
        DeepSeekReceiptAi.from_environment()
