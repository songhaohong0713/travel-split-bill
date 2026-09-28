import asyncio
import logging

import httpx
import pytest
from app.providers.deepseek_receipt_ai import DeepSeekReceiptAi, DeepSeekReceiptAiError


class FakeResponse:
    def __init__(self, content: str, status_code: int = 200) -> None:
        self._content = content
        self.status_code = status_code

    def json(self) -> dict[str, object]:
        return {"choices": [{"message": {"content": self._content}}]}


class FakeClient:
    def __init__(self, content: str, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code
        self.calls: list[dict[str, object]] = []

    async def post(self, _: str, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        return FakeResponse(self.content, self.status_code)


class TimeoutClient:
    async def post(self, _: str, **__: object) -> FakeResponse:
        raise httpx.ReadTimeout("request timed out")


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


def test_receipt_prompt_requires_line_totals_and_excludes_tax() -> None:
    client = FakeClient(
        '{"items":[{"source_text":"Tea x2","translated_text":"茶","amount":"240","currency":"JPY"}]}'
    )
    provider = DeepSeekReceiptAi("deepseek-key", client=client)

    asyncio.run(provider.recognize(b"jpeg"))

    payload = client.calls[0]["json"]
    prompt = payload["messages"][0]["content"][0]["text"]
    assert "line total" in prompt
    assert "tax" in prompt


def test_recognize_logs_only_safe_status_when_deepseek_rejects(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    provider = DeepSeekReceiptAi("deepseek-key", client=FakeClient("provider body", status_code=401))

    with pytest.raises(DeepSeekReceiptAiError):
        asyncio.run(provider.recognize(b"jpeg"))

    assert "deepseek_receipt_request_rejected status=401" in caplog.text
    assert "deepseek-key" not in caplog.text
    assert "provider body" not in caplog.text


def test_recognize_logs_safe_timeout_diagnostics(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    provider = DeepSeekReceiptAi("deepseek-key", client=TimeoutClient())

    with pytest.raises(DeepSeekReceiptAiError):
        asyncio.run(provider.recognize(b"receipt image"))

    assert "deepseek_receipt_request_failed error_type=ReadTimeout" in caplog.text
    assert "elapsed_ms=" in caplog.text
    assert "deepseek-key" not in caplog.text
    assert "receipt image" not in caplog.text
