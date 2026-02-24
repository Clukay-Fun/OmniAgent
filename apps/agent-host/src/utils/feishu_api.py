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
from typing import Any, Mapping

import json
import httpx

from src.config import Settings
from src.utils.metrics import record_credential_refresh


# region 异常与管理器
class FeishuAPIError(RuntimeError):
    """飞书 API 调用异常"""
    pass


def _is_action_unsupported_error(error_text: str) -> bool:
    normalized = str(error_text or "").lower()
    tokens = (
        "not support tag: action_list",
        "unsupported tag action",
        "schema v2 no longer support this capability",
        "no longer support this capability",
    )
    return any(token in normalized for token in tokens)


def _card_has_action_element(card: Mapping[str, Any]) -> bool:
    body_raw = card.get("body")
    body = body_raw if isinstance(body_raw, Mapping) else {}
    elements_raw = body.get("elements")
    elements = elements_raw if isinstance(elements_raw, list) else []
    for item in elements:
        if not isinstance(item, Mapping):
            continue
        tag = str(item.get("tag") or "").strip().lower()
        if tag in {"action", "action_list"}:
            return True
    return False


def _convert_card_v2_to_legacy(card: Mapping[str, Any]) -> dict[str, Any] | None:
    schema = str(card.get("schema") or "").strip()
    if schema != "2.0":
        return None
    if not _card_has_action_element(card):
        return None

    body_raw = card.get("body")
    body = body_raw if isinstance(body_raw, Mapping) else {}
    elements_raw = body.get("elements")
    elements: list[dict[str, Any]] = []
    if isinstance(elements_raw, list):
        for item in elements_raw:
            if not isinstance(item, Mapping):
                continue
            normalized = dict(item)
            if str(normalized.get("tag") or "").strip().lower() == "action_list":
                normalized["tag"] = "action"
            elements.append(normalized)
    if not elements:
        return None

    legacy: dict[str, Any] = {"elements": elements}
    for key in ("config", "header", "card_link", "i18n_elements", "i18n_header"):
        value = card.get(key)
        if value is not None:
            if key == "header" and isinstance(value, Mapping):
                header = dict(value)
                icon_raw = header.get("icon")
                icon = icon_raw if isinstance(icon_raw, Mapping) else {}
                if str(icon.get("tag") or "").strip().lower() == "standard_icon":
                    header.pop("icon", None)
                legacy[key] = header
            else:
                legacy[key] = value
    return legacy


class TokenManager:
    """
    Token 管理器
    
    功能:
        - 自动获取 tenant_access_token
        - 缓存 Token 并在过期前刷新
        - 线程安全 (AsyncLock)
    """
    def __init__(
        self,
        settings: Settings,
        app_id: str | None = None,
        app_secret: str | None = None,
        org: str = "default",
    ) -> None:
        self._settings = settings
        self._app_id = str(app_id or "").strip()
        self._app_secret = str(app_secret or "").strip()
        self._org = str(org or "default").strip() or "default"
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
        app_id = self._app_id or self._settings.feishu.app_id
        app_secret = self._app_secret or self._settings.feishu.app_secret
        if not app_id or not app_secret:
            record_credential_refresh(self._org, "failed")
            raise FeishuAPIError("FEISHU app_id/app_secret is required")

        url = f"{self._settings.feishu.api_base}/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": app_id,
            "app_secret": app_secret,
        }
        async with httpx.AsyncClient(
            timeout=self._settings.feishu.message.reply_timeout,
            trust_env=False,
        ) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

        if data.get("code") != 0:
            record_credential_refresh(self._org, "failed")
            raise FeishuAPIError(data.get("msg") or "Failed to fetch token")
        token = data.get("tenant_access_token")
        expire = data.get("expire")
        if not token or not expire:
            record_credential_refresh(self._org, "failed")
            raise FeishuAPIError("Invalid token response")
        record_credential_refresh(self._org, "success")
        return token, int(expire)
# endregion


_token_managers: dict[str, TokenManager] = {}


def _normalize_credential_source(source: str | None) -> str:
    normalized = str(source or "default").strip().lower().replace("-", "_")
    return normalized or "default"


def _resolve_credentials(settings: Settings, source: str) -> tuple[str, str]:
    if source == "org_b":
        app_id = str(settings.feishu.org_b_app_id or "").strip()
        app_secret = str(settings.feishu.org_b_app_secret or "").strip()
        if not app_id or not app_secret:
            raise FeishuAPIError("FEISHU_BOT_ORG_B_APP_ID/SECRET is required for org_b credential source")
        return app_id, app_secret
    return str(settings.feishu.app_id or "").strip(), str(settings.feishu.app_secret or "").strip()


def get_token_manager(settings: Settings, credential_source: str = "default") -> TokenManager:
    source = _normalize_credential_source(credential_source)
    if source not in _token_managers:
        app_id, app_secret = _resolve_credentials(settings, source)
        _token_managers[source] = TokenManager(
            settings=settings,
            app_id=app_id,
            app_secret=app_secret,
            org=source,
        )
    return _token_managers[source]


# region 消息 API
async def send_message(
    settings: Settings,
    receive_id: str,
    msg_type: str,
    content: dict[str, object],
    reply_message_id: str | None = None,
    receive_id_type: str = "chat_id",
    credential_source: str = "default",
) -> dict[str, Any]:
    """
    发送飞书消息
    
    参数:
        settings: 配置对象
        receive_id: 接收者 ID (chat_id, open_id 等)
        msg_type: 消息类型 (text, post, interactive 等)
        content: 消息内容字典
        reply_message_id: 回复的消息 ID (可选)
        receive_id_type: 接收 ID 类型 (默认为 chat_id)
        
    返回:
        响应数据（包含 message_id）
    """
    token = await get_token_manager(settings, credential_source=credential_source).get_token()
    url = f"{settings.feishu.api_base}/im/v1/messages"
    params = {"receive_id_type": receive_id_type}
    payload: dict[str, object] = {
        "receive_id": receive_id,
        "msg_type": msg_type,
        "content": json.dumps(content, ensure_ascii=False),
    }
    if settings.feishu.message.use_reply_mode and reply_message_id:
        payload["reply_message_id"] = reply_message_id

    async with httpx.AsyncClient(
        timeout=settings.feishu.message.reply_timeout,
        trust_env=False,
    ) as client:
        try:
            response = await client.post(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            can_retry = msg_type == "interactive" and isinstance(content, Mapping)
            response_text = exc.response.text if exc.response is not None else ""
            legacy_card = _convert_card_v2_to_legacy(content) if can_retry else None
            if legacy_card is not None and _is_action_unsupported_error(response_text):
                retry_payload = dict(payload)
                retry_payload["content"] = json.dumps(legacy_card, ensure_ascii=False)
                response = await client.post(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                    json=retry_payload,
                )
                response.raise_for_status()
            else:
                raise

        data = response.json()
        if data.get("code") != 0:
            raise FeishuAPIError(data.get("msg") or "Failed to send message")
        return data.get("data", {})


async def send_status_message(
    settings: Settings,
    receive_id: str,
    status_text: str,
    reply_message_id: str | None = None,
    receive_id_type: str = "chat_id",
) -> str:
    """
    发送状态提示消息（如"正在思考..."）
    
    参数:
        settings: 配置对象
        receive_id: 接收者 ID
        status_text: 状态文本
        reply_message_id: 回复的消息 ID
        receive_id_type: 接收 ID 类型
        
    返回:
        发送的消息 ID
    """
    result = await send_message(
        settings=settings,
        receive_id=receive_id,
        msg_type="text",
        content={"text": status_text},
        reply_message_id=reply_message_id,
        receive_id_type=receive_id_type,
    )
    return result.get("message_id", "")


async def update_message(
    settings: Settings,
    message_id: str,
    msg_type: str,
    content: dict[str, object],
) -> None:
    """
    更新已发送的消息
    
    参数:
        settings: 配置对象
        message_id: 要更新的消息 ID
        msg_type: 消息类型
        content: 新的消息内容
    """
    token = await get_token_manager(settings).get_token()
    url = f"{settings.feishu.api_base}/im/v1/messages/{message_id}"
    payload = {
        "msg_type": msg_type,
        "content": json.dumps(content, ensure_ascii=False),
    }

    async with httpx.AsyncClient(
        timeout=settings.feishu.message.reply_timeout,
        trust_env=False,
    ) as client:
        try:
            response = await client.patch(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            can_retry = msg_type == "interactive" and isinstance(content, Mapping)
            response_text = exc.response.text if exc.response is not None else ""
            legacy_card = _convert_card_v2_to_legacy(content) if can_retry else None
            if legacy_card is not None and _is_action_unsupported_error(response_text):
                retry_payload = {
                    "msg_type": msg_type,
                    "content": json.dumps(legacy_card, ensure_ascii=False),
                }
                response = await client.patch(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    json=retry_payload,
                )
                response.raise_for_status()
            else:
                raise

        data = response.json()
        if data.get("code") != 0:
            raise FeishuAPIError(data.get("msg") or "Failed to update message")


async def set_message_reaction(
    settings: Settings,
    message_id: str,
    reaction_type: str,
    credential_source: str = "default",
) -> str:
    token = await get_token_manager(settings, credential_source=credential_source).get_token()
    normalized_message_id = str(message_id or "").strip()
    normalized_reaction_type = str(reaction_type or "").strip()
    if not normalized_message_id or not normalized_reaction_type:
        return ""

    url = f"{settings.feishu.api_base}/im/v1/messages/{normalized_message_id}/reactions"
    payload = {
        "reaction_type": {
            "emoji_type": normalized_reaction_type,
        },
    }
    async with httpx.AsyncClient(
        timeout=settings.feishu.message.reply_timeout,
        trust_env=False,
    ) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        try:
            data = response.json()
        except Exception:
            data = {}
        if response.status_code >= 400:
            detail = ""
            if isinstance(data, dict):
                detail = str(data.get("msg") or data.get("error") or "").strip()
            raise FeishuAPIError(detail or f"Failed to set message reaction (HTTP {response.status_code})")
        if data.get("code") != 0:
            raise FeishuAPIError(data.get("msg") or "Failed to set message reaction")
        # 返回 reaction_id，供撤回时使用
        resp_data = data.get("data") or {}
        return str(resp_data.get("reaction_id") or "")


async def delete_message_reaction(
    settings: Settings,
    message_id: str,
    reaction_id: str,
    credential_source: str = "default",
) -> None:
    """删除飞书消息上已添加的 reaction，切换阶段时勘回旧表情"""
    token = await get_token_manager(settings, credential_source=credential_source).get_token()
    normalized_message_id = str(message_id or "").strip()
    normalized_reaction_id = str(reaction_id or "").strip()
    if not normalized_message_id or not normalized_reaction_id:
        return

    url = f"{settings.feishu.api_base}/im/v1/messages/{normalized_message_id}/reactions/{normalized_reaction_id}"
    async with httpx.AsyncClient(
        timeout=settings.feishu.message.reply_timeout,
        trust_env=False,
    ) as client:
        response = await client.delete(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )
        # 404 表示 reaction 已被删除，忽略
        if response.status_code == 404:
            return
        try:
            data = response.json()
        except Exception:
            data = {}
        if response.status_code >= 400:
            detail = ""
            if isinstance(data, dict):
                detail = str(data.get("msg") or data.get("error") or "").strip()
            raise FeishuAPIError(detail or f"Failed to delete message reaction (HTTP {response.status_code})")
# endregion
