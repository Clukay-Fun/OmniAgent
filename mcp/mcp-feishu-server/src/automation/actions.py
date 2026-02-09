from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from src.config import Settings


LOGGER = logging.getLogger(__name__)


class _SafeTemplateDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return value.format_map(_SafeTemplateDict(context))
    if isinstance(value, dict):
        return {k: _render_value(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_value(item, context) for item in value]
    return value


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value

    if isinstance(value, (int, float)):
        ts = int(value)
        if ts > 10_000_000_000:
            ts = int(ts / 1000)
        return datetime.fromtimestamp(ts)

    if isinstance(value, dict):
        ts = value.get("timestamp")
        if ts is not None:
            return _parse_datetime(ts)

    text = str(value or "").strip()
    if not text:
        raise ValueError("datetime value is required")

    if text.isdigit():
        return _parse_datetime(int(text))

    normalized = text.replace("T", " ")
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            return dt.astimezone().replace(tzinfo=None)
        return dt
    except ValueError as exc:
        raise ValueError(f"invalid datetime format: {value}") from exc


def _to_unix_seconds(dt: datetime) -> str:
    return str(int(dt.timestamp()))


class ActionExecutor:
    """动作执行器：支持 log.write 与 bitable.update。"""

    def __init__(self, settings: Settings, client: Any) -> None:
        self._settings = settings
        self._client = client

    @staticmethod
    def _compose_context(context: dict[str, Any]) -> dict[str, Any]:
        fields = context.get("fields")
        merged: dict[str, Any] = dict(context)
        if isinstance(fields, dict):
            merged.update(fields)
        return merged

    async def _action_log_write(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        level = str(action.get("level") or "info").lower()
        message_template = str(action.get("message") or "")
        rendered_message = _render_value(message_template, self._compose_context(context))

        log_fn = getattr(LOGGER, level, LOGGER.info)
        log_fn("automation.log.write %s", rendered_message)
        return {
            "type": "log.write",
            "level": level,
            "message": rendered_message,
        }

    async def _action_bitable_update(
        self,
        action: dict[str, Any],
        context: dict[str, Any],
        app_token: str,
        table_id: str,
        record_id: str,
    ) -> dict[str, Any]:
        fields_template = action.get("fields")
        if not isinstance(fields_template, dict) or not fields_template:
            raise ValueError("bitable.update requires non-empty fields")

        rendered_fields = _render_value(fields_template, self._compose_context(context))
        if not isinstance(rendered_fields, dict) or not rendered_fields:
            raise ValueError("bitable.update rendered fields is empty")

        await self._client.request(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            json_body={"fields": rendered_fields},
        )

        fields = context.get("fields")
        if isinstance(fields, dict):
            fields.update(rendered_fields)

        return {
            "type": "bitable.update",
            "fields": rendered_fields,
        }

    async def _action_calendar_create(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        rendered_action = _render_value(action, self._compose_context(context))
        if not isinstance(rendered_action, dict):
            raise ValueError("calendar.create action payload is invalid")

        calendar_id = str(
            rendered_action.get("calendar_id")
            or self._settings.calendar.default_calendar_id
            or ""
        ).strip()
        if not calendar_id:
            raise ValueError("calendar.create requires calendar_id")

        summary = str(
            rendered_action.get("summary")
            or rendered_action.get("summary_template")
            or ""
        ).strip()
        if not summary:
            raise ValueError("calendar.create requires summary/summary_template")

        description = str(
            rendered_action.get("description")
            or rendered_action.get("description_template")
            or ""
        )

        timezone = str(
            rendered_action.get("timezone") or self._settings.calendar.timezone or "Asia/Shanghai"
        ).strip()
        need_notification = bool(rendered_action.get("need_notification", True))
        rrule = str(rendered_action.get("rrule") or "").strip()

        start_value: Any = rendered_action.get("start_at")
        start_field = str(rendered_action.get("start_field") or "").strip()
        if start_value is None and start_field:
            fields = context.get("fields")
            if isinstance(fields, dict):
                start_value = fields.get(start_field)
        if start_value is None:
            raise ValueError("calendar.create requires start_at or start_field")
        start_dt = _parse_datetime(start_value)

        end_value: Any = rendered_action.get("end_at")
        end_field = str(rendered_action.get("end_field") or "").strip()
        if end_value is None and end_field:
            fields = context.get("fields")
            if isinstance(fields, dict):
                end_value = fields.get(end_field)

        if end_value is not None:
            end_dt = _parse_datetime(end_value)
        else:
            duration = int(
                rendered_action.get("duration_minutes")
                or self._settings.calendar.default_duration_minutes
                or 30
            )
            if duration <= 0:
                duration = 30
            end_dt = start_dt + timedelta(minutes=duration)

        if end_dt <= start_dt:
            raise ValueError("calendar.create end_at must be later than start_at")

        body: dict[str, Any] = {
            "summary": summary,
            "description": description,
            "need_notification": need_notification,
            "start_time": {
                "timestamp": _to_unix_seconds(start_dt),
                "timezone": timezone,
            },
            "end_time": {
                "timestamp": _to_unix_seconds(end_dt),
                "timezone": timezone,
            },
        }
        if rrule:
            body["rrule"] = rrule

        response = await self._client.request(
            "POST",
            f"/calendar/v4/calendars/{calendar_id}/events",
            json_body=body,
        )
        data = response.get("data") or {}
        event = data.get("event") if isinstance(data.get("event"), dict) else {}
        event_id = event.get("event_id") or data.get("event_id") or ""
        event_url = event.get("url") or event.get("html_link") or data.get("url") or ""

        return {
            "type": "calendar.create",
            "calendar_id": calendar_id,
            "event_id": str(event_id),
            "event_url": str(event_url),
            "summary": summary,
            "start_at": start_dt.strftime("%Y-%m-%d %H:%M"),
            "end_at": end_dt.strftime("%Y-%m-%d %H:%M"),
            "timezone": timezone,
        }

    async def run_actions(
        self,
        actions: list[dict[str, Any]],
        context: dict[str, Any],
        app_token: str,
        table_id: str,
        record_id: str,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for action in actions:
            action_type = str(action.get("type") or "").strip()
            if action_type == "log.write":
                results.append(await self._action_log_write(action, context))
                continue
            if action_type == "bitable.update":
                results.append(
                    await self._action_bitable_update(action, context, app_token, table_id, record_id)
                )
                continue
            if action_type == "calendar.create":
                results.append(await self._action_calendar_create(action, context))
                continue
            raise ValueError(f"unsupported action type: {action_type}")
        return results
