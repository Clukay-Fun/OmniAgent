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
from src.utils.metrics import record_automation_enqueue, record_feishu_event, record_schema_watcher_alert


@dataclass
class EventRouteResult:
    """事件分发结果。"""

    status: str
    reason: str = ""
    handler: str = ""
    payload: dict[str, Any] | None = None


class EventHandler(Protocol):
    def handle(self, envelope: EventEnvelope) -> EventRouteResult: ...


class AutomationEnqueuer(Protocol):
    def enqueue_record_changed(self, event_payload: dict[str, Any]) -> bool | None: ...


class NoopAutomationEnqueuer:
    """默认空实现：不执行真实入队。"""

    def enqueue_record_changed(self, event_payload: dict[str, Any]) -> bool | None:
        _ = event_payload
        return False


@dataclass
class RecordChangedPayload:
    event_id: str
    event_type: str
    app_token: str
    table_id: str
    record_id: str
    changed_fields: list[str]
    occurred_at: str
    raw_fragment: dict[str, Any]


@dataclass
class FieldChangedPayload:
    event_id: str
    app_token: str
    table_id: str
    field_id: str
    field_name: str
    change_type: str
    raw_event: dict[str, Any]


@dataclass
class CalendarChangedPayload:
    event_id: str
    calendar_id: str
    calendar_event_id: str
    summary: str
    raw_event: dict[str, Any]


class RecordChangedHandler:
    """最小 Record Changed 事件处理器。"""

    EVENT_TYPE = "drive.file.bitable_record_changed_v1"

    def __init__(
        self,
        automation_enqueuer: AutomationEnqueuer | None = None,
        automation_engine: Any | None = None,
    ) -> None:
        self._automation_enqueuer = self._resolve_automation_enqueuer(automation_enqueuer, automation_engine)
        self._logger = logging.getLogger(__name__)

    @staticmethod
    def _safe_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        return {}

    def handle(self, envelope: EventEnvelope) -> EventRouteResult:
        parsed = self._parse_payload(envelope)
        enqueue_payload = self._build_enqueue_payload(parsed)
        enqueue_status = self._enqueue_automation(enqueue_payload)
        record_automation_enqueue(parsed.event_type, enqueue_status)
        self._logger.info(
            "record_changed event received",
            extra={
                "event_code": "event_router.record_changed.received",
                "event_id": parsed.event_id,
                "event_type": parsed.event_type,
                "app_token": parsed.app_token,
                "table_id": parsed.table_id,
                "record_id": parsed.record_id,
                "changed_fields": parsed.changed_fields,
                "changed_field_count": len(parsed.changed_fields),
                "automation_enqueue_status": enqueue_status,
                "occurred_at": parsed.occurred_at,
            },
        )
        return EventRouteResult(
            status="handled",
            reason="record_changed",
            handler="record_changed",
            payload={
                "event_id": parsed.event_id,
                "event_type": parsed.event_type,
                "app_token": parsed.app_token,
                "table_id": parsed.table_id,
                "record_id": parsed.record_id,
                "changed_fields": parsed.changed_fields,
                "occurred_at": parsed.occurred_at,
                "automation_enqueue": enqueue_status,
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
        occurred_at = self._extract_occurred_at(event, object_payload)
        return RecordChangedPayload(
            event_id=envelope.event_id,
            event_type=envelope.event_type,
            app_token=app_token,
            table_id=table_id,
            record_id=record_id,
            changed_fields=changed_fields,
            occurred_at=occurred_at,
            raw_fragment=object_payload or event,
        )

    def _extract_occurred_at(self, event: dict[str, Any], object_payload: dict[str, Any]) -> str:
        raw_value = (
            event.get("occurred_at")
            or event.get("occurredAt")
            or event.get("event_time")
            or event.get("timestamp")
            or object_payload.get("occurred_at")
            or object_payload.get("occurredAt")
            or object_payload.get("event_time")
            or object_payload.get("timestamp")
            or ""
        )
        return str(raw_value).strip()

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

    def _resolve_automation_enqueuer(
        self,
        automation_enqueuer: AutomationEnqueuer | None,
        automation_engine: Any | None,
    ) -> AutomationEnqueuer:
        if automation_enqueuer is not None:
            return automation_enqueuer
        if automation_engine is None:
            return NoopAutomationEnqueuer()
        enqueue_hook = getattr(automation_engine, "enqueue_record_changed", None)
        if callable(enqueue_hook):
            return automation_engine
        legacy_hook = getattr(automation_engine, "on_record_changed", None)
        if callable(legacy_hook):
            return _LegacyAutomationEnqueuer(automation_engine=automation_engine)
        return NoopAutomationEnqueuer()

    def _build_enqueue_payload(self, payload: RecordChangedPayload) -> dict[str, Any]:
        return {
            "event_id": payload.event_id,
            "event_type": payload.event_type,
            "app_token": payload.app_token,
            "table_id": payload.table_id,
            "record_id": payload.record_id,
            "changed_fields": payload.changed_fields,
            "occurred_at": payload.occurred_at,
            "raw_fragment": payload.raw_fragment,
        }

    def _enqueue_automation(self, payload: dict[str, Any]) -> str:
        enqueue = getattr(self._automation_enqueuer, "enqueue_record_changed", None)
        if not callable(enqueue):
            return "not_available"
        self._logger.info(
            "automation enqueue attempt",
            extra={
                "event_code": "event_router.record_changed.enqueue_attempt",
                "event_id": payload.get("event_id", ""),
                "event_type": payload.get("event_type", ""),
            },
        )
        try:
            result = enqueue(payload)
            if result is False:
                return "not_available"
            return "enqueued"
        except Exception:
            self._logger.exception(
                "automation enqueue failed",
                extra={
                    "event_code": "event_router.record_changed.enqueue_failed",
                    "event_id": payload.get("event_id", ""),
                },
            )
            return "failed"


class _LegacyAutomationEnqueuer:
    def __init__(self, automation_engine: Any) -> None:
        self._automation_engine = automation_engine

    def enqueue_record_changed(self, event_payload: dict[str, Any]) -> bool | None:
        hook = getattr(self._automation_engine, "on_record_changed", None)
        if not callable(hook):
            return False
        hook(
            event_id=event_payload.get("event_id", ""),
            app_token=event_payload.get("app_token", ""),
            table_id=event_payload.get("table_id", ""),
            record_id=event_payload.get("record_id", ""),
            changed_fields=event_payload.get("changed_fields", []),
            raw_event=event_payload.get("raw_fragment", {}),
        )
        return True


class FieldChangedHandler:
    """最小 Field Changed 事件处理器。"""

    EVENT_TYPE = "drive.file.bitable_field_changed_v1"

    def __init__(self, schema_sync: Any | None = None) -> None:
        self._schema_sync = schema_sync
        self._logger = logging.getLogger(__name__)

    @staticmethod
    def _safe_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        return {}

    def handle(self, envelope: EventEnvelope) -> EventRouteResult:
        try:
            parsed = self._parse_payload(envelope)
        except Exception:
            record_schema_watcher_alert("parse_failure")
            self._logger.warning(
                "field_changed payload parse failed",
                extra={
                    "event_code": "event_router.field_changed.parse_failed",
                    "event_id": envelope.event_id,
                },
                exc_info=True,
            )
            return EventRouteResult(status="ignored", reason="field_changed_parse_failed", handler="field_changed")

        alert_marked = False
        if not parsed.table_id:
            alert_marked = True
            record_schema_watcher_alert("parse_failure")
            self._logger.warning(
                "field_changed missing table_id",
                extra={
                    "event_code": "event_router.field_changed.missing_table_id",
                    "event_id": parsed.event_id,
                    "change_type": parsed.change_type,
                },
            )

        invalidate_status = self._invalidate_schema_cache(parsed)
        hook_status = self._call_schema_hook(parsed)

        if parsed.change_type in {"rename", "type_change"}:
            alert_marked = True
            record_schema_watcher_alert(parsed.change_type)
            self._logger.warning(
                "field_changed alert: schema cache invalidated",
                extra={
                    "event_code": "event_router.field_changed.alert",
                    "event_id": parsed.event_id,
                    "app_token": parsed.app_token,
                    "table_id": parsed.table_id,
                    "field_id": parsed.field_id,
                    "field_name": parsed.field_name,
                    "change_type": parsed.change_type,
                    "schema_invalidate_status": invalidate_status,
                    "schema_sync_hook_status": hook_status,
                    "alert": True,
                },
            )
        else:
            log_method = self._logger.info if parsed.change_type in {"add", "remove"} else self._logger.warning
            log_method(
                "field_changed event received",
                extra={
                    "event_code": "event_router.field_changed.received",
                    "event_id": parsed.event_id,
                    "app_token": parsed.app_token,
                    "table_id": parsed.table_id,
                    "field_id": parsed.field_id,
                    "field_name": parsed.field_name,
                    "change_type": parsed.change_type,
                    "schema_invalidate_status": invalidate_status,
                    "schema_sync_hook_status": hook_status,
                    "alert": alert_marked,
                },
            )

        return EventRouteResult(
            status="handled",
            reason="field_changed",
            handler="field_changed",
            payload={
                "event_id": parsed.event_id,
                "app_token": parsed.app_token,
                "table_id": parsed.table_id,
                "field_id": parsed.field_id,
                "field_name": parsed.field_name,
                "schema_sync_hook": hook_status,
            },
        )

    def _parse_payload(self, envelope: EventEnvelope) -> FieldChangedPayload:
        event = self._safe_dict(envelope.event)
        object_payload = self._safe_dict(event.get("object"))
        app_token = str(
            event.get("app_token") or object_payload.get("app_token") or object_payload.get("app_token_id") or ""
        ).strip()
        table_id = str(event.get("table_id") or object_payload.get("table_id") or "").strip()
        field_id = str(event.get("field_id") or event.get("fieldId") or object_payload.get("field_id") or "").strip()
        field_name = str(
            event.get("field_name") or event.get("fieldName") or object_payload.get("field_name") or ""
        ).strip()
        change_type = self._extract_change_type(event, object_payload)
        return FieldChangedPayload(
            event_id=envelope.event_id,
            app_token=app_token,
            table_id=table_id,
            field_id=field_id,
            field_name=field_name,
            change_type=change_type,
            raw_event=event,
        )

    def _extract_change_type(self, event: dict[str, Any], object_payload: dict[str, Any]) -> str:
        raw_value = (
            event.get("change_type")
            or event.get("changeType")
            or event.get("field_change_type")
            or event.get("fieldChangeType")
            or event.get("operation")
            or event.get("action")
            or object_payload.get("change_type")
            or object_payload.get("changeType")
            or object_payload.get("operation")
            or object_payload.get("action")
            or ""
        )
        normalized = str(raw_value).strip().lower().replace("-", "_")
        if normalized in {"add", "added", "create", "created"}:
            return "add"
        if normalized in {"remove", "removed", "delete", "deleted", "drop", "dropped"}:
            return "remove"
        if normalized in {"rename", "renamed", "name_change", "field_name_changed"}:
            return "rename"
        if normalized in {"type_change", "field_type_change", "change_type", "type_changed"}:
            return "type_change"
        return "unknown"

    def _invalidate_schema_cache(self, payload: FieldChangedPayload) -> str:
        if not payload.table_id:
            return "missing_table_id"
        if self._schema_sync is None:
            return "not_available"

        schema_cache = getattr(self._schema_sync, "schema_cache", None)
        invalidate = getattr(schema_cache, "invalidate", None)
        if callable(invalidate):
            try:
                invalidate(payload.table_id)
                return "called"
            except Exception:
                self._logger.exception(
                    "schema_cache invalidate failed",
                    extra={
                        "event_code": "event_router.field_changed.invalidate_failed",
                        "event_id": payload.event_id,
                        "table_id": payload.table_id,
                    },
                )
                return "failed"
        return "not_available"

    def _call_schema_hook(self, payload: FieldChangedPayload) -> str:
        if self._schema_sync is None:
            return "not_available"
        hook = getattr(self._schema_sync, "on_field_changed", None)
        if not callable(hook):
            return "not_implemented"
        try:
            hook(
                event_id=payload.event_id,
                app_token=payload.app_token,
                table_id=payload.table_id,
                field_id=payload.field_id,
                field_name=payload.field_name,
                raw_event=payload.raw_event,
            )
            return "called"
        except Exception:
            self._logger.exception(
                "schema_sync on_field_changed hook failed",
                extra={
                    "event_code": "event_router.field_changed.hook_failed",
                    "event_id": payload.event_id,
                },
            )
            return "failed"


class CalendarChangedHandler:
    """最小 Calendar Changed 事件处理器。"""

    EVENT_TYPES = {
        "calendar.calendar.event.changed_v4",
        "calendar.calendar.changed_v4",
    }

    def __init__(self, reminder_engine: Any | None = None) -> None:
        self._reminder_engine = reminder_engine
        self._logger = logging.getLogger(__name__)

    @staticmethod
    def _safe_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        return {}

    def handle(self, envelope: EventEnvelope) -> EventRouteResult:
        parsed = self._parse_payload(envelope)
        hook_status = self._call_reminder_hook(parsed)
        self._logger.info(
            "calendar_changed event received",
            extra={
                "event_code": "event_router.calendar_changed.received",
                "event_id": parsed.event_id,
                "calendar_id": parsed.calendar_id,
                "calendar_event_id": parsed.calendar_event_id,
                "summary": parsed.summary,
                "reminder_hook_status": hook_status,
            },
        )
        return EventRouteResult(
            status="handled",
            reason="calendar_changed",
            handler="calendar_changed",
            payload={
                "event_id": parsed.event_id,
                "calendar_id": parsed.calendar_id,
                "calendar_event_id": parsed.calendar_event_id,
                "summary": parsed.summary,
                "reminder_hook": hook_status,
            },
        )

    def _parse_payload(self, envelope: EventEnvelope) -> CalendarChangedPayload:
        event = self._safe_dict(envelope.event)
        calendar_payload = self._safe_dict(event.get("calendar"))
        calendar_event_payload = self._safe_dict(event.get("calendar_event"))
        calendar_id = str(
            event.get("calendar_id")
            or calendar_payload.get("calendar_id")
            or calendar_event_payload.get("calendar_id")
            or ""
        ).strip()
        calendar_event_id = str(
            event.get("event_id") or calendar_event_payload.get("event_id") or calendar_event_payload.get("id") or ""
        ).strip()
        summary = str(
            event.get("summary")
            or event.get("title")
            or calendar_payload.get("summary")
            or calendar_event_payload.get("summary")
            or calendar_event_payload.get("title")
            or ""
        ).strip()
        return CalendarChangedPayload(
            event_id=envelope.event_id,
            calendar_id=calendar_id,
            calendar_event_id=calendar_event_id,
            summary=summary,
            raw_event=event,
        )

    def _call_reminder_hook(self, payload: CalendarChangedPayload) -> str:
        if self._reminder_engine is None:
            return "not_available"
        hook = getattr(self._reminder_engine, "on_calendar_changed", None)
        if not callable(hook):
            return "not_implemented"
        try:
            hook(
                event_id=payload.event_id,
                calendar_id=payload.calendar_id,
                calendar_event_id=payload.calendar_event_id,
                summary=payload.summary,
                raw_event=payload.raw_event,
            )
            return "called"
        except Exception:
            self._logger.exception(
                "reminder_engine on_calendar_changed hook failed",
                extra={
                    "event_code": "event_router.calendar_changed.hook_failed",
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
        automation_enqueuer: AutomationEnqueuer | None = None,
        automation_engine: Any | None = None,
        schema_sync: Any | None = None,
        reminder_engine: Any | None = None,
    ) -> None:
        self._enabled_types = {item.strip() for item in (enabled_types or []) if str(item).strip()}
        self._handlers: dict[str, EventHandler] = {}
        if handlers:
            for event_type, handler in handlers.items():
                self.register_handler(event_type, handler)
        if RecordChangedHandler.EVENT_TYPE not in self._handlers:
            self.register_handler(
                RecordChangedHandler.EVENT_TYPE,
                RecordChangedHandler(
                    automation_enqueuer=automation_enqueuer,
                    automation_engine=automation_engine,
                ),
            )
        if FieldChangedHandler.EVENT_TYPE not in self._handlers:
            self.register_handler(
                FieldChangedHandler.EVENT_TYPE,
                FieldChangedHandler(schema_sync=schema_sync),
            )
        for event_type in CalendarChangedHandler.EVENT_TYPES:
            if event_type not in self._handlers:
                self.register_handler(
                    event_type,
                    CalendarChangedHandler(reminder_engine=reminder_engine),
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
