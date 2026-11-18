"""Private CloudBase PG Storage; service credentials never leave the backend."""

import os
from urllib.parse import quote, urljoin, urlparse

import httpx


class ReceiptStorageError(Exception):
    pass


class ReceiptStorage:
    def __init__(
        self, env_id: str, api_key: str, *, client: httpx.AsyncClient | None = None
    ):
        self.origin = f"https://{env_id}.api.tcloudbasegateway.com"
        self._api_key = api_key
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_environment(cls):
        env = os.getenv("CLOUDBASE_ENV_ID")
        key = os.getenv("CLOUDBASE_APIKEY") or os.getenv("CLOUDBASE_API_KEY")
        if not env or not key:
            raise ReceiptStorageError("Receipt storage is not configured")
        return cls(env, key)

    async def aclose(self):
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _path(self, key: str, *, signed: bool = False):
        return (
            "/v1/storages/object/"
            + ("sign/" if signed else "")
            + "receipts/"
            + quote(key, safe="/")
        )

    async def _request(self, method, path, *, missing_ok=False, **kwargs):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        try:
            response = await self._client.request(
                method,
                self.origin + path,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    **kwargs.pop("headers", {}),
                },
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise ReceiptStorageError("Receipt storage is unavailable") from exc
        if missing_ok and response.status_code == 404:
            return response
        if response.is_error:
            # Never return a platform response body: it may include credentials or URLs.
            raise ReceiptStorageError(
                f"Receipt storage is unavailable (HTTP {response.status_code})"
            )
        return response

    async def upload(self, key: str, image: bytes, content_type: str = "image/jpeg"):
        await self._request(
            "POST",
            self._path(key),
            content=image,
            headers={
                "Content-Type": content_type,
                "x-upsert": "false",
                "Cache-Control": "no-store",
            },
        )

    async def exists(self, key: str) -> bool:
        return (
            await self._request("HEAD", self._path(key), missing_ok=True)
        ).status_code != 404

    async def download(self, key: str) -> tuple[bytes, str]:
        response = await self._request("GET", self._path(key), missing_ok=True)
        if response.status_code == 404:
            raise FileNotFoundError("Receipt original is missing")
        data = response.content
        if not data or len(data) > 5_000_000:
            raise ReceiptStorageError("Receipt image size is invalid")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return data, "image/png"
        if data.startswith(b"\xff\xd8"):
            return data, "image/jpeg"
        raise ReceiptStorageError("Receipt image format is invalid")

    async def signed_url(self, key: str) -> str:
        response = await self._request(
            "POST", self._path(key, signed=True), json={"expiresIn": 300}
        )
        try:
            data = response.json()
            raw = data.get("fullSignedURL") or data.get("signedURL")
            if not isinstance(raw, str) or not raw:
                raise ValueError("missing signed URL")
            url = urljoin(self.origin + "/", raw)
            parsed = urlparse(url)
            if (
                parsed.scheme != "https"
                or parsed.netloc != urlparse(self.origin).netloc
                or not parsed.path.startswith("/v1/storages/object/sign/receipts/")
            ):
                raise ValueError("invalid signed URL")
        except (ValueError, TypeError, AttributeError) as exc:
            raise ReceiptStorageError(
                "Receipt storage returned an invalid URL"
            ) from exc
        return url

    async def delete(self, key: str):
        await self._request("DELETE", self._path(key), missing_ok=True)
