"""
描述: 占位符修复辅助脚本。
主要功能:
    - 修复规则中任务描述占位符格式
    - 辅助迁移旧版模板字段表达
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from dotenv import load_dotenv

from src.config import get_settings
from src.feishu.client import FeishuClient


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return str(value.get("text"))
        nested = value.get("value")
        if isinstance(nested, list):
            parts: list[str] = []
            for item in nested:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(str(item.get("text")))
            if parts:
                return "".join(parts)
        return ""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(str(item.get("text")))
        if parts:
            return "".join(parts)
        return ""
    return str(value)


def _extract_source_record_id(value: Any) -> str:
    text = _as_text(value).strip()
    if text:
        return text
    if isinstance(value, dict):
        for key in ("id", "record_id"):
            if value.get(key):
                return str(value.get(key)).strip()
    return ""


async def _list_records(client: FeishuClient, app_token: str, table_id: str, field_names: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page_token = ""
    while True:
        payload: dict[str, Any] = {
            "page_size": 200,
            "field_names": field_names,
        }
        if page_token:
            payload["page_token"] = page_token
        response = await client.request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
            json_body=payload,
        )
        data = response.get("data") or {}
        items = data.get("items") or []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    records.append(item)

        if not data.get("has_more"):
            break
        page_token = str(data.get("page_token") or "")
        if not page_token:
            break
    return records


async def main() -> int:
    parser = argparse.ArgumentParser(description="Repair placeholder task descriptions in overview table")
    parser.add_argument("--source-table", default="tblnKgT7iNOQwN7J")
    parser.add_argument("--target-table", default="tblDM7BJ8nMoIZzV")
    parser.add_argument("--app-token", default="")
    parser.add_argument("--placeholder", default="{任务描述}")
    args = parser.parse_args()

    load_dotenv()
    get_settings.cache_clear()
    settings = get_settings()
    app_token = str(args.app_token or settings.bitable.default_app_token or "").strip()
    if not app_token:
        raise RuntimeError("missing app token")

    client = FeishuClient(settings)
    target_records = await _list_records(
        client,
        app_token,
        str(args.target_table),
        ["源记录ID", "任务描述"],
    )

    repaired = 0
    skipped = 0

    for record in target_records:
        record_id = str(record.get("record_id") or "").strip()
        fields = record.get("fields")
        if not isinstance(fields, dict):
            skipped += 1
            continue

        current_desc = _as_text(fields.get("任务描述")).strip()
        if current_desc != str(args.placeholder):
            skipped += 1
            continue

        source_record_id = _extract_source_record_id(fields.get("源记录ID"))
        if not source_record_id:
            skipped += 1
            continue

        source_response = await client.request(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables/{args.source_table}/records/{source_record_id}",
            params={"field_names": '["任务描述"]'},
        )
        source_data = source_response.get("data") or {}
        source_record = source_data.get("record") or source_data
        source_fields = source_record.get("fields") if isinstance(source_record, dict) else {}
        if not isinstance(source_fields, dict):
            source_fields = {}

        real_desc = _as_text(source_fields.get("任务描述")).strip()
        if not real_desc or real_desc == str(args.placeholder):
            skipped += 1
            continue

        await client.request(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{args.target_table}/records/{record_id}",
            json_body={"fields": {"任务描述": real_desc}},
        )
        repaired += 1

    print(f"repaired={repaired} skipped={skipped} total={len(target_records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
