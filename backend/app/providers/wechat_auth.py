import os
from typing import Any

import httpx


class WechatAuthError(Exception):
    pass


class WechatCode2Session:
    endpoint = "https://api.weixin.qq.com/sns/jscode2session"

    def __init__(self, app_id: str, app_secret: str, client: httpx.AsyncClient | None = None) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.client = client

    @classmethod
    def from_environment(cls) -> "WechatCode2Session":
        app_id = os.getenv("WECHAT_APP_ID")
        app_secret = os.getenv("WECHAT_APP_SECRET")
        if not app_id or not app_secret:
            raise WechatAuthError("WeChat credentials are not configured")
        return cls(app_id, app_secret)

    async def openid_for_code(self, code: str) -> str:
        params = {"appid": self.app_id, "secret": self.app_secret, "js_code": code, "grant_type": "authorization_code"}
        if self.client is None:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(self.endpoint, params=params)
        else:
            response = await self.client.get(self.endpoint, params=params)
        response.raise_for_status()
        payload: Any = response.json()
        openid = payload.get("openid") if isinstance(payload, dict) else None
        if not isinstance(openid, str) or not openid:
            raise WechatAuthError("WeChat login failed")
        return openid