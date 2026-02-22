"""
描述: 飞书事件分发器（骨架）
主要功能:
    - 识别事件类型并分发到消息处理或占位处理
    - 为非消息事件提供统一埋点
    - 保持可扩展的 event_type 路由结构
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Protocol

from src.adapters.channels.feishu.event_adapter import EventEnvelope
from src.utils.metrics import record_feishu_event


@dataclass
class EventRouteResult:
    """事件分发结果。"""

    status: str
    reason: str = ""
    handler: str = ""
    payload: dict[str, Any] | None = None


class EventHandler(Protocol):
    def handle(self, envelope: EventEnvelope) -> EventRouteResult: ...


@dataclass
class RecordChangedPayload:
    event_id: str
    app_token: str
    table_id: str
    record_id: str
    changed_fields: list[str]
    raw_event: dict[str, Any]


class RecordChangedHandler:
    """最小 Record Changed 事件处理器。"""

    EVENT_TYPE = "drive.file.bitable_record_changed_v1"

    def __init__(self, automation_engine: Any | None = None) -> None:
        self._automation_engine = automation_engine
        self._logger = logging.getLogger(__name__)

    @staticmethod
    def _safe_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        return {}

    def handle(self, envelope: EventEnvelope) -> EventRouteResult:
        parsed = self._parse_payload(envelope)
        hook_status = self._call_automation_hook(parsed)
        self._logger.info(
            "record_changed event received",
            extra={
                "event_code": "event_router.record_changed.received",
                "event_id": parsed.event_id,
                "app_token": parsed.app_token,
                "table_id": parsed.table_id,
                "record_id": parsed.record_id,
                "changed_fields": parsed.changed_fields,
                "changed_field_count": len(parsed.changed_fields),
                "automation_hook_status": hook_status,
            },
        )
        return EventRouteResult(
            status="handled",
            reason="record_changed",
            handler="record_changed",
            payload={
                "event_id": parsed.event_id,
                "app_token": parsed.app_token,
                "table_id": parsed.table_id,
                "record_id": parsed.record_id,
                "changed_fields": parsed.changed_fields,
                "automation_hook": hook_status,
            },
        )

    def _parse_payload(self, envelope: EventEnvelope) -> RecordChangedPayload:
        event = self._safe_dict(envelope.event)
        object_payload = self._safe_dict(event.get("object"))
        app_token = str(
            event.get("app_token") or object_payload.get("app_token") or object_payload.get("app_token_id") or ""
        ).strip()
        table_id = str(event.get("table_id") or object_payload.get("table_id") or "").strip()
        record_id = str(event.get("record_id") or object_payload.get("record_id") or "").strip()
        changed_fields = self._extract_changed_fields(event)
        return RecordChangedPayload(
            event_id=envelope.event_id,
            app_token=app_token,
            table_id=table_id,
            record_id=record_id,
            changed_fields=changed_fields,
            raw_event=event,
        )

    def _extract_changed_fields(self, event: dict[str, Any]) -> list[str]:
        candidates = (
            event.get("changed_fields"),
            event.get("changedFields"),
            event.get("changed_field_names"),
            event.get("changedFieldNames"),
        )
        for candidate in candidates:
            fields = self._normalize_changed_fields(candidate)
            if fields:
                return fields
        return []

    def _normalize_changed_fields(self, value: Any) -> list[str]:
        if isinstance(value, dict):
            return [str(name).strip() for name in value.keys() if str(name).strip()]
        if isinstance(value, list):
            fields: list[str] = []
            for item in value:
                field_name = ""
                if isinstance(item, str):
                    field_name = item
                elif isinstance(item, dict):
                    field_name = str(item.get("field_name") or item.get("field") or item.get("name") or "")
                field_name = field_name.strip()
                if field_name and field_name not in fields:
                    fields.append(field_name)
            return fields
        return []

    def _call_automation_hook(self, payload: RecordChangedPayload) -> str:
        if self._automation_engine is None:
            return "not_available"
        hook = getattr(self._automation_engine, "on_record_changed", None)
        if not callable(hook):
            return "not_implemented"
        try:
            hook(
                event_id=payload.event_id,
                app_token=payload.app_token,
                table_id=payload.table_id,
                record_id=payload.record_id,
                changed_fields=payload.changed_fields,
                raw_event=payload.raw_event,
            )
            return "called"
        except Exception:
            self._logger.exception(
                "automation on_record_changed hook failed",
                extra={
                    "event_code": "event_router.record_changed.hook_failed",
                    "event_id": payload.event_id,
                },
            )
            return "failed"


class FeishuEventRouter:
    """飞书事件路由器（第一阶段骨架）。"""

    MESSAGE_EVENT_TYPES = {"im.message.receive_v1"}

    def __init__(
        self,
        enabled_types: list[str] | None = None,
        handlers: dict[str, EventHandler] | None = None,
        automation_engine: Any | None = None,
    ) -> None:
        self._enabled_types = {item.strip() for item in (enabled_types or []) if str(item).strip()}
        self._handlers: dict[str, EventHandler] = {}
        if handlers:
            for event_type, handler in handlers.items():
                self.register_handler(event_type, handler)
        if RecordChangedHandler.EVENT_TYPE not in self._handlers:
            self.register_handler(
                RecordChangedHandler.EVENT_TYPE,
                RecordChangedHandler(automation_engine=automation_engine),
            )

    def register_handler(self, event_type: str, handler: EventHandler) -> None:
        normalized_event_type = str(event_type or "").strip()
        if not normalized_event_type:
            return
        self._handlers[normalized_event_type] = handler

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

        handler = self._handlers.get(event_type)
        if handler is None:
            record_feishu_event(event_type, "ignored")
            return EventRouteResult(status="ignored", reason="event_not_implemented")

        result = handler.handle(envelope)
        record_feishu_event(event_type, result.status)
        return result


def get_enabled_types(settings: Any) -> list[str] | None:
    webhook_settings = getattr(settings, "webhook", None)
    event_settings = getattr(webhook_settings, "events", None)
    enabled_types = getattr(event_settings, "enabled_types", None)
    if isinstance(enabled_types, list):
        return [str(item) for item in enabled_types]
    return None
