"""
描述: 飞书 Tenant Access Token 管理器
主要功能:
    - 自动获取/刷新 Tenant Token
    - 内存缓存与过期机制
    - thread-safe
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from src.config import Settings


# region 异常与管理器
class FeishuAuthError(RuntimeError):
    """飞书认证相关异常"""
    pass


class TenantAccessTokenManager:
    """
    租户访问令牌管理器 (Tenant Access Token)

    功能:
        - 维护 tenant_access_token 的生命周期
        - 自动处理 token 刷新 (提前 refresh_ahead_seconds 刷新)
    """
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        """获取有效的 Token (自动刷新)"""
        async with self._lock:
            now = time.time()
            refresh_ahead = self._settings.feishu.token.refresh_ahead_seconds
            if self._token and now < (self._expires_at - refresh_ahead):
                return self._token

            token, expires_in = await self._fetch_token()
            self._token = token
            self._expires_at = now + expires_in
            return token

    async def cache_snapshot(self) -> dict[str, int | bool]:
        """返回 token 缓存状态快照。"""
        async with self._lock:
            now = time.time()
            expires_in_seconds = 0
            if self._token and self._expires_at > now:
                expires_in_seconds = max(0, int(self._expires_at - now))
            return {
                "cached": bool(self._token),
                "expires_in_seconds": expires_in_seconds,
            }

    async def _fetch_token(self) -> tuple[str, int]:
        """请求飞书接口获取新 Token"""
        if not self._settings.feishu.app_id or not self._settings.feishu.app_secret:
            raise FeishuAuthError("FEISHU app_id/app_secret is required")

        url = f"{self._settings.feishu.api_base}/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self._settings.feishu.app_id,
            "app_secret": self._settings.feishu.app_secret,
        }

        retries = max(0, int(self._settings.feishu.request.max_retries))
        delay = max(0.0, float(self._settings.feishu.request.retry_delay))
        timeout = self._settings.feishu.request.timeout

        data: dict[str, Any] = {}
        fetched = False
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                    response = await client.post(url, json=payload)
                    response.raise_for_status()
                    raw_data = response.json()
                    if not isinstance(raw_data, dict):
                        raise FeishuAuthError("Invalid tenant token response")
                    data = raw_data
                    fetched = True
                break
            except (httpx.HTTPError, ValueError) as exc:
                if attempt >= retries:
                    error_message = str(exc).strip()
                    if error_message:
                        raise FeishuAuthError(
                            f"Failed to fetch tenant token: {exc.__class__.__name__}: {error_message}"
                        ) from exc
                    raise FeishuAuthError(
                        f"Failed to fetch tenant token: {exc.__class__.__name__}"
                    ) from exc
                await asyncio.sleep(delay * (2 ** attempt))

        if not fetched:
            raise FeishuAuthError("Failed to fetch tenant token")

        if data.get("code") != 0:
            raise FeishuAuthError(data.get("msg") or "Failed to fetch tenant token")

        token = data.get("tenant_access_token")
        expire = data.get("expire")
        if not token or not expire:
            raise FeishuAuthError("Invalid tenant token response")
        return token, int(expire)
# endregion
