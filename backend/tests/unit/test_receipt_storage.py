import httpx
import pytest
from app.providers.receipt_storage import ReceiptStorage, ReceiptStorageError


@pytest.mark.anyio
async def test_private_storage_preserves_bytes_and_only_returns_short_signed_url():
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer backend-secret"
        if request.method == "HEAD":
            return httpx.Response(200)
        if "/sign/" in request.url.path:
            assert request.content == b'{"expiresIn":300}'
            return httpx.Response(
                200,
                json={
                    "signedURL": "/v1/storages/object/sign/receipts/u/t/i.jpg?token=short"
                },
            )
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        store = ReceiptStorage("test-env", "backend-secret", client=client)
        await store.upload("u/t/i.jpg", b"original-jpeg")
        assert await store.exists("u/t/i.jpg") is True
        url = await store.signed_url("u/t/i.jpg")
        await store.delete("u/t/i.jpg")
    assert calls[0].content == b"original-jpeg"
    assert calls[0].headers["Content-Type"] == "image/jpeg"
    assert calls[0].headers["x-upsert"] == "false"
    assert calls[0].url.path == "/v1/storages/object/receipts/u/t/i.jpg"
    assert (
        url
        == "https://test-env.api.tcloudbasegateway.com/v1/storages/object/sign/receipts/u/t/i.jpg?token=short"
    )
    assert "backend-secret" not in url


@pytest.mark.anyio
async def test_missing_object_is_distinct_from_storage_outage():
    codes = iter([404, 404, 503])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(next(codes)))
    ) as client:
        store = ReceiptStorage("test-env", "secret", client=client)
        assert await store.exists("missing.jpg") is False
        await store.delete("missing.jpg")
        with pytest.raises(ReceiptStorageError, match="unavailable"):
            await store.exists("missing.jpg")


@pytest.mark.anyio
async def test_provider_rejects_cross_origin_signed_url_and_does_not_expose_upstream_secrets():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"fullSignedURL": "https://other.example/leak"}
            )
        )
    ) as client:
        with pytest.raises(ReceiptStorageError):
            await ReceiptStorage("test-env", "secret", client=client).signed_url(
                "i.jpg"
            )


@pytest.mark.anyio
async def test_authenticated_download_preserves_bytes_and_rejects_missing_or_invalid_objects():
    content = b"\x89PNG\r\n\x1a\noriginal"

    def handler(r):
        assert r.method == "GET"
        assert r.headers["Authorization"] == "Bearer server-secret"
        if r.url.path.endswith("missing.png"):
            return httpx.Response(404)
        if r.url.path.endswith("bad.png"):
            return httpx.Response(200, content=b"not an image")
        return httpx.Response(200, content=content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        store = ReceiptStorage("test-env", "server-secret", client=client)
        assert await store.download("image.png") == (content, "image/png")
        with pytest.raises(FileNotFoundError):
            await store.download("missing.png")
        with pytest.raises(ReceiptStorageError):
            await store.download("bad.png")
