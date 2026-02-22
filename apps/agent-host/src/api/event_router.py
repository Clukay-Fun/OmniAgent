"""
描述: 飞书事件分发器（骨架）
主要功能:
    - 识别事件类型并分发到消息处理或占位处理
    - 为非消息事件提供统一埋点
    - 保持可扩展的 event_type 路由结构
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.adapters.channels.feishu.event_adapter import EventEnvelope
from src.utils.metrics import record_feishu_event


@dataclass
class EventRouteResult:
    """事件分发结果。"""

    status: str
    reason: str = ""


class FeishuEventRouter:
    """飞书事件路由器（第一阶段骨架）。"""

    MESSAGE_EVENT_TYPES = {"im.message.receive_v1"}

    def __init__(self, enabled_types: list[str] | None = None) -> None:
        self._enabled_types = {item.strip() for item in (enabled_types or []) if str(item).strip()}

    def route(self, envelope: EventEnvelope) -> EventRouteResult:
        event_type = envelope.event_type or "unknown"

        if self._enabled_types and event_type not in self._enabled_types:
            record_feishu_event(event_type, "ignored")
            return EventRouteResult(status="ignored", reason="event_type_disabled")

        if event_type in self.MESSAGE_EVENT_TYPES:
            if envelope.message is None:
                record_feishu_event(event_type, "ignored")
                return EventRouteResult(status="ignored", reason="missing_message_body")
            record_feishu_event(event_type, "accepted")
            return EventRouteResult(status="accepted", reason="message")

        # 非消息事件先落埋点，不阻塞主链路
        record_feishu_event(event_type, "ignored")
        return EventRouteResult(status="ignored", reason="event_not_implemented")


def get_enabled_types(settings: Any) -> list[str] | None:
    webhook_settings = getattr(settings, "webhook", None)
    event_settings = getattr(webhook_settings, "events", None)
    enabled_types = getattr(event_settings, "enabled_types", None)
    if isinstance(enabled_types, list):
        return [str(item) for item in enabled_types]
    return None
