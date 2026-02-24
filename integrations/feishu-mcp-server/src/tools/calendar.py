"""
描述: 飞书日历工具
主要功能:
    - 创建日历事件（支持重复规则 RRULE）
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from src.tools.base import BaseTool
from src.tools.registry import ToolRegistry


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError("start_at is required")

    normalized = text.replace("T", " ")
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
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
        raise ValueError(f"Invalid datetime format: {value}") from exc


def _to_unix_seconds(dt: datetime) -> str:
    return str(int(dt.timestamp()))


@ToolRegistry.register
class CalendarEventCreateTool(BaseTool):
    """创建飞书日历事件"""

    name = "feishu.v1.calendar.event.create"
    description = "Create calendar event in Feishu calendar."
    parameters = {
        "type": "object",
        "properties": {
            "calendar_id": {"type": "string", "description": "目标日历 ID"},
            "summary": {"type": "string", "description": "事件标题"},
            "description": {"type": "string", "description": "事件描述"},
            "start_at": {"type": "string", "description": "开始时间 YYYY-MM-DD HH:MM"},
            "end_at": {"type": "string", "description": "结束时间 YYYY-MM-DD HH:MM"},
            "duration_minutes": {"type": "integer", "description": "结束时间缺省时的默认时长（分钟）", "default": 30},
            "timezone": {"type": "string", "description": "时区，默认 Asia/Shanghai"},
            "rrule": {"type": "string", "description": "重复规则（RFC 5545），例如 FREQ=DAILY"},
            "need_notification": {"type": "boolean", "description": "是否发送通知", "default": True},
        },
        "required": ["summary", "start_at"],
    }

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        settings = self.context.settings

        calendar_id = str(params.get("calendar_id") or settings.calendar.default_calendar_id or "").strip()
        summary = str(params.get("summary") or "").strip()
        description = str(params.get("description") or "").strip()
        timezone = str(params.get("timezone") or settings.calendar.timezone or "Asia/Shanghai").strip()
        rrule = str(params.get("rrule") or "").strip()
        need_notification = bool(params.get("need_notification", True))

        if not calendar_id:
            raise ValueError("calendar_id is required")
        if not summary:
            raise ValueError("summary is required")

        start_dt = _parse_datetime(params.get("start_at"))
        if params.get("end_at"):
            end_dt = _parse_datetime(params.get("end_at"))
        else:
            duration = int(params.get("duration_minutes") or settings.calendar.default_duration_minutes or 30)
            if duration <= 0:
                duration = 30
            end_dt = start_dt + timedelta(minutes=duration)

        if end_dt <= start_dt:
            raise ValueError("end_at must be later than start_at")

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

        response = await self.context.client.request(
            "POST",
            f"/calendar/v4/calendars/{calendar_id}/events",
            json_body=body,
        )

        data = response.get("data") or {}
        event = data.get("event") if isinstance(data.get("event"), dict) else {}

        event_id = event.get("event_id") or data.get("event_id") or ""
        event_url = event.get("url") or event.get("html_link") or data.get("url") or ""

        return {
            "calendar_id": calendar_id,
            "event_id": str(event_id),
            "event_url": str(event_url),
            "summary": summary,
            "start_at": start_dt.strftime("%Y-%m-%d %H:%M"),
            "end_at": end_dt.strftime("%Y-%m-%d %H:%M"),
            "timezone": timezone,
            "rrule": rrule,
            "raw": data,
        }
