"""
Bitable tools.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
import ast
import re

from src.tools.base import BaseTool
from src.tools.registry import ToolRegistry
from src.utils.url_builder import build_record_url


def _build_keyword_condition(keyword: str, field: str) -> dict[str, Any]:
    return {
        "field_name": field,
        "operator": "contains",
        "value": [keyword],
    }


def _build_date_conditions(field: str, date_from: str | None, date_to: str | None) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []
    if date_from:
        conditions.append({
            "field_name": field,
            "operator": "isGreaterEqual",
            "value": [date_from],
        })
    if date_to:
        conditions.append({
            "field_name": field,
            "operator": "isLessEqual",
            "value": [date_to],
        })
    return conditions


def _build_filters(filters: dict[str, Any]) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []
    for field, value in filters.items():
        if value is None:
            continue
        conditions.append({
            "field_name": field,
            "operator": "is",
            "value": [value],
        })
    return conditions


def _format_timestamp(value: int | float) -> str:
    try:
        return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return str(value)


def _normalize_field_name(name: str) -> str:
    return re.sub(r"\s+", "", name)


def _parse_text_blob(value: str) -> str | None:
    raw_value = value.strip()
    if not raw_value.startswith("{"):
        return None
    if "'text'" not in raw_value and '"text"' not in raw_value:
        return None
    try:
        parsed = ast.literal_eval(raw_value)
    except (ValueError, SyntaxError):
        return None
    if isinstance(parsed, dict):
        text = parsed.get("text")
        if isinstance(text, str):
            return text
    return None


def parse_field_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float)) and value > 1_000_000_000_000:
        return _format_timestamp(value)
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            if "name" in value[0]:
                return ", ".join([str(item.get("name", "")) for item in value])
            if "text" in value[0]:
                return ", ".join([str(item.get("text", "")) for item in value])
        return ", ".join([str(item) for item in value])
    if isinstance(value, dict):
        if "name" in value:
            return str(value.get("name"))
        if "text" in value:
            return str(value.get("text"))
        return str(value)
    if isinstance(value, str):
        parsed_text = _parse_text_blob(value)
        return parsed_text if parsed_text is not None else value
    return str(value)


async def _fetch_field_names(tool: "BitableSearchTool", app_token: str, table_id: str) -> set[str]:
    try:
        response = await tool.context.client.request(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
        )
        data = response.get("data") or {}
        items = data.get("items") or []
        return {item.get("field_name") for item in items if item.get("field_name")}
    except Exception:
        return set()


@ToolRegistry.register
class BitableSearchTool(BaseTool):
    name = "feishu.v1.bitable.search"
    description = "Search bitable records with keyword and date range."

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        keyword = params.get("keyword") or ""
        date_from = params.get("date_from")
        date_to = params.get("date_to")
        filters = params.get("filters") or {}

        settings = self.context.settings
        app_token = params.get("app_token") or settings.bitable.default_app_token
        table_id = params.get("table_id") or settings.bitable.default_table_id
        view_id = params.get("view_id") or settings.bitable.default_view_id

        if not app_token or not table_id:
            return {"records": [], "total": 0}

        field_names = await _fetch_field_names(self, app_token, table_id)
        normalized_lookup = {
            _normalize_field_name(name): name for name in field_names
        }

        hearing_field = settings.bitable.field_mapping.get("hearing_date", "开庭日")
        if hearing_field:
            resolved = normalized_lookup.get(_normalize_field_name(hearing_field))
            if resolved:
                hearing_field = resolved
            elif field_names and hearing_field not in field_names:
                hearing_field = ""

        keyword_fields = settings.bitable.search.searchable_fields
        keyword_candidates = []
        for field in keyword_fields:
            if not field_names:
                keyword_candidates.append(field)
                continue
            resolved = normalized_lookup.get(_normalize_field_name(field))
            if resolved:
                keyword_candidates.append(resolved)
        if not keyword_candidates and hearing_field:
            keyword_candidates = [hearing_field]

        keyword_conditions: list[dict[str, Any]] = []
        if keyword:
            for field in keyword_candidates:
                keyword_conditions.append(_build_keyword_condition(keyword, field))

        date_conditions: list[dict[str, Any]] = []
        if hearing_field:
            date_conditions = _build_date_conditions(hearing_field, date_from, date_to)
        extra_conditions = _build_filters(filters)

        conjunction = "and"
        if keyword_conditions and not date_conditions and not extra_conditions:
            conditions = keyword_conditions
            if len(keyword_conditions) > 1:
                conjunction = "or"
        else:
            conditions = []
            if keyword_conditions:
                conditions.append(keyword_conditions[0])
            conditions.extend(date_conditions)
            conditions.extend(extra_conditions)

        limit = int(params.get("limit") or settings.bitable.search.default_limit)
        limit = min(limit, settings.bitable.search.max_records)
        page_token = params.get("page_token")

        if field_names:
            return_fields = set()
            for name in settings.bitable.field_mapping.values():
                resolved = normalized_lookup.get(_normalize_field_name(name))
                if resolved:
                    return_fields.add(resolved)
            if hearing_field:
                return_fields.add(hearing_field)
            field_names = sorted(return_fields)
        else:
            field_names = []

        payload: dict[str, Any] = {
            "page_size": limit,
        }
        if view_id:
            payload["view_id"] = view_id
        if field_names:
            payload["field_names"] = field_names
        if conditions:
            payload["filter"] = {
                "conjunction": conjunction,
                "conditions": conditions,
            }
        if hearing_field:
            payload["sort"] = [{"field_name": hearing_field, "desc": False}]
        if page_token:
            payload["page_token"] = page_token

        response = await self.context.client.request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
            json_body=payload,
        )
        data = response.get("data") or {}
        items = data.get("items") or []

        records = []
        for item in items:
            record_id = item.get("record_id") or item.get("recordId") or item.get("id")
            raw_fields = item.get("fields") or {}
            fields_text: dict[str, Any] = {}
            for key, value in raw_fields.items():
                parsed = parse_field_value(value)
                normalized_key = _normalize_field_name(key)
                fields_text[key] = parsed
                if normalized_key != key:
                    fields_text[normalized_key] = parsed
            if record_id:
                record_url = build_record_url(
                    settings.bitable.domain,
                    app_token,
                    table_id,
                    record_id,
                    view_id=view_id,
                )
            else:
                record_url = ""
            records.append({
                "record_id": record_id,
                "fields": raw_fields,
                "fields_text": fields_text,
                "record_url": record_url,
            })

        total = data.get("total") or len(records)
        return {
            "records": records,
            "total": total,
            "has_more": data.get("has_more", False),
            "page_token": data.get("page_token") or "",
        }


@ToolRegistry.register
class BitableRecordGetTool(BaseTool):
    name = "feishu.v1.bitable.record.get"
    description = "Get a single bitable record by record_id."

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        record_id = params.get("record_id")
        if not record_id:
            return {"record_id": None, "fields": {}, "record_url": ""}

        settings = self.context.settings
        app_token = params.get("app_token") or settings.bitable.default_app_token
        table_id = params.get("table_id") or settings.bitable.default_table_id
        view_id = params.get("view_id") or settings.bitable.default_view_id

        response = await self.context.client.request(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
        )
        data = response.get("data") or {}
        raw_fields = data.get("record", {}).get("fields") or data.get("fields") or {}
        fields_text: dict[str, Any] = {}
        for key, value in raw_fields.items():
            parsed = parse_field_value(value)
            normalized_key = _normalize_field_name(key)
            fields_text[key] = parsed
            if normalized_key != key:
                fields_text[normalized_key] = parsed
        record_url = build_record_url(
            settings.bitable.domain,
            app_token,
            table_id,
            record_id,
            view_id=view_id,
        )
        return {
            "record_id": record_id,
            "fields": raw_fields,
            "fields_text": fields_text,
            "record_url": record_url,
        }
