from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping


def build_auto_reminder_items(table_name: str, fields: Mapping[str, Any]) -> list[dict[str, str]]:
    table = str(table_name or "")
    reminder_defs = {
        "案件": {
            "开庭日": (3, "开庭提醒"),
            "管辖权异议截止日": (3, "管辖权异议截止提醒"),
            "举证截止日": (3, "举证截止提醒"),
            "查封到期日": (30, "查封到期提醒"),
            "反诉截止日": (3, "反诉截止提醒"),
            "上诉截止日": (3, "上诉截止提醒"),
        },
        "合同": {
            "合同结束日期": (30, "合同到期提醒"),
        },
        "招投标": {
            "标书购买截止时间": (2, "标书购买截止提醒"),
            "截标时间": (3, "截标提醒"),
            "保证金截止日期": (2, "保证金截止提醒"),
        },
    }

    picked: dict[str, tuple[int, str]] = {}
    for key, defs in reminder_defs.items():
        if key in table:
            picked = defs
            break
    if not picked:
        return []

    items: list[dict[str, str]] = []
    for field_name, config in picked.items():
        days_before, label = config
        target = _parse_date(fields.get(field_name))
        if target is None:
            continue
        remind_date = target - timedelta(days=days_before)
        if remind_date < date.today():
            continue
        items.append(
            {
                "field": field_name,
                "content": f"{label}（{field_name}）",
                "remind_time": f"{remind_date.isoformat()} 09:00",
                "priority": "medium",
            }
        )
    return items


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip().lstrip("：:")
    if not text:
        return None
    normalized = text.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = normalized.replace("/", "-").replace(".", "-")
    if "T" in normalized:
        normalized = normalized.split("T", 1)[0]
    if " " in normalized:
        normalized = normalized.split(" ", 1)[0]
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None
