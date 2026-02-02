"""
Feishu API helpers for message sending.
"""

from __future__ import annotations

import asyncio
import time

import json
import httpx

from src.config import Settings


class FeishuAPIError(RuntimeError):
    pass


class TokenManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        async with self._lock:
            if self._token and time.time() < self._expires_at - 300:
                return self._token
            token, expires_in = await self._fetch_token()
            self._token = token
            self._expires_at = time.time() + expires_in
            return token

    async def _fetch_token(self) -> tuple[str, int]:
        if not self._settings.feishu.app_id or not self._settings.feishu.app_secret:
            raise FeishuAPIError("FEISHU app_id/app_secret is required")

        url = f"{self._settings.feishu.api_base}/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self._settings.feishu.app_id,
            "app_secret": self._settings.feishu.app_secret,
        }
        async with httpx.AsyncClient(timeout=self._settings.feishu.message.reply_timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

        if data.get("code") != 0:
            raise FeishuAPIError(data.get("msg") or "Failed to fetch token")
        token = data.get("tenant_access_token")
        expire = data.get("expire")
        if not token or not expire:
            raise FeishuAPIError("Invalid token response")
        return token, int(expire)


async def send_message(
    settings: Settings,
    receive_id: str,
    msg_type: str,
    content: dict[str, object],
    reply_message_id: str | None = None,
    receive_id_type: str = "chat_id",
) -> None:
    token_manager = TokenManager(settings)
    token = await token_manager.get_token()
    url = f"{settings.feishu.api_base}/im/v1/messages"
    params = {"receive_id_type": receive_id_type}
    payload: dict[str, object] = {
        "receive_id": receive_id,
        "msg_type": msg_type,
        "content": json.dumps(content, ensure_ascii=False),
    }
    if settings.feishu.message.use_reply_mode and reply_message_id:
        payload["reply_message_id"] = reply_message_id

    async with httpx.AsyncClient(timeout=settings.feishu.message.reply_timeout) as client:
        response = await client.post(
            url,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise FeishuAPIError(data.get("msg") or "Failed to send message")
