import os

import httpx


class CloudBasePgError(Exception):
    pass


class CloudBasePgConfigurationError(CloudBasePgError):
    pass


class CloudBasePgUnavailable(CloudBasePgError):
    pass


class CloudBasePgRequestError(CloudBasePgError):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


class CloudBasePgClient:
    def __init__(
        self,
        env_id: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = f"https://{env_id}.api.tcloudbasegateway.com/v1/rdb/rest"
        self._api_key = api_key
        self._client = client

    @classmethod
    def from_environment(cls) -> "CloudBasePgClient":
        env_id = os.getenv("CLOUDBASE_ENV_ID")
        api_key = os.getenv("CLOUDBASE_APIKEY") or os.getenv("CLOUDBASE_API_KEY")
        if not env_id or not api_key:
            raise CloudBasePgConfigurationError(
                "CloudBase environment ID and API key must be configured"
            )
        return cls(env_id, api_key)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        payload: object | None = None,
    ) -> object:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=10.0)
        try:
            response = await client.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=payload,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise CloudBasePgUnavailable("CloudBase PostgreSQL request failed") from exc
        finally:
            if owns_client:
                await client.aclose()
        if response.status_code in {401, 403}:
            raise CloudBasePgConfigurationError("CloudBase PostgreSQL authorization failed")
        if response.status_code >= 500:
            raise CloudBasePgUnavailable("CloudBase PostgreSQL is unavailable")
        if response.is_error:
            raise CloudBasePgRequestError(
                response.status_code, "CloudBase PostgreSQL request was rejected"
            )
        if not response.content:
            return None
        return response.json()

    async def rpc(self, name: str, payload: dict[str, object]) -> object:
        return await self.request("POST", f"/rpc/{name}", payload=payload)
