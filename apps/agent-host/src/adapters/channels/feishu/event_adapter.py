"""
描述: 飞书事件适配器
主要功能:
    - 将飞书原始 payload 解析为标准 DataClass
    - 统一 Webhook 与 WebSocket 的消息字段提取
    - 隔离渠道字段命名，减少上层直接解析字典
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MessageEvent:
    """标准化后的消息事件。"""

    event_type: str
    event_id: str
    message_id: str
    chat_id: str
    chat_type: str
    message_type: str
    content: str
    sender_open_id: str
    sender_user_id: str
    sender_type: str


@dataclass
class EventEnvelope:
    """统一事件外层封装。"""

    event_type: str
    event_id: str
    message: MessageEvent | None = None
    event: dict[str, Any] | None = None


class FeishuEventAdapter:
    """飞书事件适配器。"""

    MESSAGE_EVENT_TYPE = "im.message.receive_v1"

    @staticmethod
    def _safe_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        return {}

    @classmethod
    def from_webhook_payload(cls, payload: dict[str, Any]) -> EventEnvelope:
        header: dict[str, Any] = cls._safe_dict(payload.get("header"))
        event: dict[str, Any] = cls._safe_dict(payload.get("event"))
        event_type = str(header.get("event_type") or payload.get("type") or "").strip()
        event_id = str(header.get("event_id") or payload.get("event_id") or "").strip()

        message: dict[str, Any] = cls._safe_dict(event.get("message"))
        sender: dict[str, Any] = cls._safe_dict(event.get("sender"))
        sender_id: dict[str, Any] = cls._safe_dict(sender.get("sender_id"))

        message_id = str(message.get("message_id") or message.get("messageId") or "").strip()
        chat_id = str(message.get("chat_id") or "").strip()
        chat_type = str(message.get("chat_type") or "").strip()
        message_type = str(message.get("message_type") or "").strip()
        content = str(message.get("content") or "")
        sender_open_id = str(sender_id.get("open_id") or "").strip()
        sender_user_id = str(sender_id.get("user_id") or "").strip()
        sender_type = str(sender.get("sender_type") or "").strip()

        message_event: MessageEvent | None = None
        if message_id or chat_id or message_type:
            if not event_type:
                event_type = cls.MESSAGE_EVENT_TYPE
            message_event = MessageEvent(
                event_type=event_type,
                event_id=event_id,
                message_id=message_id,
                chat_id=chat_id,
                chat_type=chat_type,
                message_type=message_type,
                content=content,
                sender_open_id=sender_open_id,
                sender_user_id=sender_user_id,
                sender_type=sender_type,
            )

        return EventEnvelope(event_type=event_type, event_id=event_id, message=message_event, event=event)

    @classmethod
    def from_ws_event(cls, data: Any) -> MessageEvent | None:
        event = getattr(data, "event", None)
        if event is None:
            return None

        message = getattr(event, "message", None)
        sender = getattr(event, "sender", None)
        if message is None or sender is None:
            return None

        sender_id = getattr(sender, "sender_id", None)
        return MessageEvent(
            event_type=cls.MESSAGE_EVENT_TYPE,
            event_id="",
            message_id=str(getattr(message, "message_id", "") or "").strip(),
            chat_id=str(getattr(message, "chat_id", "") or "").strip(),
            chat_type=str(getattr(message, "chat_type", "") or "").strip(),
            message_type=str(getattr(message, "message_type", "") or "").strip(),
            content=str(getattr(message, "content", "") or ""),
            sender_open_id=str(getattr(sender_id, "open_id", "") or "").strip(),
            sender_user_id=str(getattr(sender_id, "user_id", "") or "").strip(),
            sender_type=str(getattr(sender, "sender_type", "") or "").strip(),
        )
