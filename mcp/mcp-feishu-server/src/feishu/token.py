"""
Feishu tenant access token manager.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from src.config import Settings


class FeishuAuthError(RuntimeError):
    pass


class TenantAccessTokenManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        async with self._lock:
            now = time.time()
            refresh_ahead = self._settings.feishu.token.refresh_ahead_seconds
            if self._token and now < (self._expires_at - refresh_ahead):
                return self._token

            token, expires_in = await self._fetch_token()
            self._token = token
            self._expires_at = now + expires_in
            return token

    async def _fetch_token(self) -> tuple[str, int]:
        if not self._settings.feishu.app_id or not self._settings.feishu.app_secret:
            raise FeishuAuthError("FEISHU app_id/app_secret is required")

        url = f"{self._settings.feishu.api_base}/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self._settings.feishu.app_id,
            "app_secret": self._settings.feishu.app_secret,
        }

        async with httpx.AsyncClient(timeout=self._settings.feishu.request.timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

        if data.get("code") != 0:
            raise FeishuAuthError(data.get("msg") or "Failed to fetch tenant token")

        token = data.get("tenant_access_token")
        expire = data.get("expire")
        if not token or not expire:
            raise FeishuAuthError("Invalid tenant token response")
        return token, int(expire)
