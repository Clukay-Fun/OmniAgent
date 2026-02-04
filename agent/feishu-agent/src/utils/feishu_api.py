"""
描述: 飞书 API 基础工具库
主要功能:
    - Token 管理与缓存 (tenant_access_token)
    - 消息发送接口封装 (send_message)
    - 统一错误处理 (FeishuAPIError)
"""

from __future__ import annotations

import asyncio
import time

import json
import httpx

from src.config import Settings


# region 异常与管理器
class FeishuAPIError(RuntimeError):
    """飞书 API 调用异常"""
    pass


class TokenManager:
    """
    Token 管理器
    
    功能:
        - 自动获取 tenant_access_token
        - 缓存 Token 并在过期前刷新
        - 线程安全 (AsyncLock)
    """
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        """获取有效的访问 Token (带缓存)"""
        async with self._lock:
            if self._token and time.time() < self._expires_at - 300:
                return self._token
            token, expires_in = await self._fetch_token()
            self._token = token
            self._expires_at = time.time() + expires_in
            return token

    async def _fetch_token(self) -> tuple[str, int]:
        """从飞书接口请求新 Token"""
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


        return token, int(expire)
# endregion


# region 消息 API
async def send_message(
    settings: Settings,
    receive_id: str,
    msg_type: str,
    content: dict[str, object],
    reply_message_id: str | None = None,
    receive_id_type: str = "chat_id",
) -> None:
    """
    发送飞书消息
    
    参数:
        settings: 配置对象
        receive_id: 接收者 ID (chat_id, open_id 等)
        msg_type: 消息类型 (text, post, interactive 等)
        content: 消息内容字典
        reply_message_id: 回复的消息 ID (可选)
        receive_id_type: 接收 ID 类型 (默认为 chat_id)
    """
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
# endregion
