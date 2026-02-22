from __future__ import annotations

from typing import Any


def build_record_url(table_id: str | None, record_id: str | None, raw_url: str | None = None) -> str:
    direct = str(raw_url or "").strip()
    if direct:
        return direct
    table = str(table_id or "").strip()
    record = str(record_id or "").strip()
    if not table or not record:
        return ""
    return f"https://feishu.cn/base/table/{table}?record={record}&table={table}"


def build_record_link_line(record_id: str | None, record_url: str | None) -> str:
    rid = str(record_id or "").strip()
    url = str(record_url or "").strip()
    if not rid or not url:
        return ""
    return f"📎 [查看原记录]({url})"


def extract_record_id(record: dict[str, Any]) -> str:
    return str(record.get("record_id") or "").strip()
