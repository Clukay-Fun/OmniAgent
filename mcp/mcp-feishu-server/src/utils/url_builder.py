"""
URL builders for Feishu resources.
"""

from __future__ import annotations


def build_record_url(
    domain: str,
    app_token: str,
    table_id: str,
    record_id: str,
    view_id: str | None = None,
) -> str:
    base = f"https://{domain}.feishu.cn/base/{app_token}?table={table_id}"
    if view_id:
        base = f"{base}&view={view_id}"
    return f"{base}&record={record_id}"
