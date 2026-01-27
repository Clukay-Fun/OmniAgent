"""
Feishu API client wrapper.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from src.config import Settings
from src.feishu.token import TenantAccessTokenManager


class FeishuAPIError(RuntimeError):
    def __init__(self, code: int, message: str, detail: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class FeishuClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._token_manager = TenantAccessTokenManager(settings)

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        token = await self._token_manager.get_token()
        url = f"{self._settings.feishu.api_base}{path}"
        retries = self._settings.feishu.request.max_retries
        delay = self._settings.feishu.request.retry_delay
        timeout = self._settings.feishu.request.timeout

        request_headers = {"Authorization": f"Bearer {token}"}
        if headers:
            request_headers.update(headers)

        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.request(
                        method,
                        url,
                        params=params,
                        json=json_body,
                        headers=request_headers,
                    )
                response.raise_for_status()
                payload = response.json()
                if payload.get("code") not in (0, None):
                    raise FeishuAPIError(
                        code=int(payload.get("code")),
                        message=payload.get("msg") or "Feishu API error",
                        detail=payload,
                    )
                return payload
            except (httpx.HTTPError, FeishuAPIError) as exc:
                if attempt >= retries:
                    if isinstance(exc, FeishuAPIError):
                        raise
                    raise FeishuAPIError(code=500, message=str(exc)) from exc
                await asyncio.sleep(delay * (2 ** attempt))

        raise FeishuAPIError(code=500, message="Feishu API request failed")
