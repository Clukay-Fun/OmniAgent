"""
描述: 飞书开放平台 API 客户端
主要功能:
    - 封装 HTTP 请求与鉴权
    - 自动处理 Access Token 注入
    - 统一错误处理与重试机制
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from src.config import Settings
from src.feishu.token import TenantAccessTokenManager


@dataclass
class FeishuAPIError(RuntimeError):
    """飞书 API 调用异常"""
    code: int
    message: str
    detail: Any | None = None

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


# region 飞书客户端
class FeishuClient:
    """
    飞书 API 客户端

    功能:
        - 统一封装 API 请求
        - 自动管理 Tenant Access Token
    """
    def __init__(self, settings: Settings) -> None:
        """
        初始化客户端

        参数:
            settings: 全局配置对象
        """
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
        """
        执行 API 请求

        参数:
            method: HTTP 方法 (GET/POST/PUT/DELETE)
            path: API 路径 (不含 Base URL)
            params: 查询参数
            json_body: JSON 请求体
            headers: 额外请求头

        返回:
            响应 JSON 数据

        抛出:
            FeishuAPIError: API 错误或网络异常
        """
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
                async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                    response = await client.request(
                        method,
                        url,
                        params=params,
                        json=json_body,
                        headers=request_headers,
                    )
                payload: dict[str, Any] = {}
                try:
                    raw_payload = response.json()
                    if isinstance(raw_payload, dict):
                        payload = raw_payload
                except ValueError:
                    payload = {}

                if response.status_code >= 400:
                    code = payload.get("code")
                    message = payload.get("msg") or payload.get("message")
                    if code is not None:
                        raise FeishuAPIError(
                            code=int(code),
                            message=message or f"HTTP {response.status_code}",
                            detail=payload,
                        )
                    raise FeishuAPIError(
                        code=int(response.status_code),
                        message=f"HTTP {response.status_code}: {response.text}",
                        detail=payload or response.text,
                    )

                if payload.get("code") not in (0, None):
                    raise FeishuAPIError(
                        code=int(payload.get("code")),
                        message=payload.get("msg") or "Feishu API error",
                        detail=payload,
                    )
                return payload
            except FeishuAPIError:
                raise
            except httpx.HTTPError as exc:
                if attempt >= retries:
                    raise FeishuAPIError(code=500, message=str(exc)) from exc
                await asyncio.sleep(delay * (2 ** attempt))

        raise FeishuAPIError(code=500, message="Feishu API request failed")
# endregion
