"""
描述: 多维表格 (Bitable) 工具集
主要功能:
    - 搜索记录 (支持关键词、时间范围、自定义筛选)
    - 获取单条记录详情
    - 创建和更新记录
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
import ast
import re

from src.tools.base import BaseTool
from src.tools.registry import ToolRegistry
from src.utils.cache import TTLCache
from src.utils.url_builder import build_record_url


_TABLES_CACHE = TTLCache(max_size=5, ttl_seconds=600)
_SCHEMA_CACHE = TTLCache(max_size=20, ttl_seconds=600)

_FIELD_TYPE_NAMES = {
    1: "文本",
    2: "数字",
    3: "单选",
    4: "多选",
    5: "日期",
    7: "复选框",
    11: "人员",
    13: "电话",
    15: "超链接",
    17: "附件",
    18: "单向关联",
    19: "公式",
    20: "双向关联",
    21: "地理位置",
    22: "群组",
    23: "创建时间",
    1001: "创建人",
    1002: "修改人",
    1003: "修改时间",
}

_DATE_FIELD_TYPES = {5, 6, 7}


# region 辅助函数
def _build_keyword_condition(keyword: str, field: str) -> dict[str, Any]:
    """构建关键词搜索条件"""
    return {
        "field_name": field,
        "operator": "contains",
        "value": [keyword],
    }


def _build_date_conditions(field: str, date_from: str | None, date_to: str | None) -> list[dict[str, Any]]:
    """构建日期范围筛选条件"""
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
    """构建自定义字段筛选条件"""
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
    """格式化时间戳 (毫秒) 为可读字符串"""
    try:
        tz = timezone(timedelta(hours=8))
        return (
            datetime.fromtimestamp(value / 1000, tz=timezone.utc)
            .astimezone(tz)
            .strftime("%Y-%m-%d %H:%M")
        )
    except (OverflowError, OSError, ValueError):
        return str(value)


def _normalize_field_name(name: str) -> str:
    """归一化字段名 (去除空白字符)"""
    return re.sub(r"\s+", "", name)


def _parse_text_blob(value: str) -> str | None:
    """尝试解析飞书富文本 Blob 结构"""
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
    """
    解析飞书字段值 (多态处理)
    
    处理:
        - 时间戳转字符串
        - 对象/列表转字符串描述
        - 富文本解析
    """
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


async def _fetch_tables(
    tool: BaseTool,
    app_token: str,
    refresh: bool = False,
) -> list[dict[str, str]]:
    """获取多维表格表列表 (带缓存)"""
    cache_key = app_token
    if not refresh:
        cached = _TABLES_CACHE.get(cache_key)
        if cached is not None:
            return cached

    response = await tool.context.client.request(
        "GET",
        f"/bitable/v1/apps/{app_token}/tables",
    )
    data = response.get("data") or {}
    items = data.get("items") or []
    tables = []
    for item in items:
        table_id = item.get("table_id") or item.get("tableId")
        table_name = item.get("name") or item.get("table_name")
        if not table_id or not table_name:
            continue
        tables.append({"table_id": table_id, "table_name": table_name})

    _TABLES_CACHE.set(cache_key, tables)
    return tables


async def _fetch_fields_info(
    tool: BaseTool,
    app_token: str,
    table_id: str,
    refresh: bool = False,
) -> dict[str, int]:
    """获取数据表字段定义元数据 (带缓存)"""
    cache_key = f"{app_token}:{table_id}"
    if not refresh:
        cached = _SCHEMA_CACHE.get(cache_key)
        if cached is not None:
            return cached

    try:
        response = await tool.context.client.request(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
        )
        data = response.get("data") or {}
        items = data.get("items") or []
        fields: dict[str, int] = {}
        for item in items:
            name = item.get("field_name")
            if not name:
                continue
            field_type = item.get("field_type")
            if field_type is None:
                field_type = item.get("type")
            try:
                fields[name] = int(field_type) if field_type is not None else -1
            except (TypeError, ValueError):
                fields[name] = -1
        _SCHEMA_CACHE.set(cache_key, fields)
        return fields
    except Exception:
        return {}


def _build_schema(fields_info: dict[str, int]) -> list[dict[str, Any]]:
    schema = []
    for name, field_type in fields_info.items():
        schema.append({
            "name": name,
            "type": field_type,
            "type_name": _FIELD_TYPE_NAMES.get(field_type, "未知"),
        })
    return sorted(schema, key=lambda item: item.get("name") or "")


def _resolve_return_fields(
    field_names: set[str],
    normalized_lookup: dict[str, str],
    settings: Any,
    extra_fields: list[str] | None = None,
) -> list[str]:
    if not field_names:
        return []
    return_fields: set[str] = set()
    for name in settings.bitable.field_mapping.values():
        resolved = normalized_lookup.get(_normalize_field_name(name))
        if resolved:
            return_fields.add(resolved)
    for name in extra_fields or []:
        resolved = normalized_lookup.get(_normalize_field_name(name))
        if resolved:
            return_fields.add(resolved)
        elif name in field_names:
            return_fields.add(name)
    return sorted(return_fields)


def _parse_date_text(value: Any) -> date | None:
    """解析自然语言或格式化日期字符串"""
    if not value:
        return None
    if isinstance(value, (int, float)):
        tz = timezone(timedelta(hours=8))
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).astimezone(tz).date()
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y/%m/%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            return None
    return None


async def _search_records(
    tool: BaseTool,
    app_token: str,
    table_id: str,
    view_id: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = await tool.context.client.request(
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
        record_url = ""
        if record_id:
            record_url = build_record_url(
                tool.context.settings.bitable.domain,
                app_token,
                table_id,
                record_id,
                view_id=view_id,
            )
        records.append({
            "record_id": record_id,
            "fields": raw_fields,
            "fields_text": fields_text,
            "record_url": record_url,
        })

    return {
        "records": records,
        "total": data.get("total") or len(records),
        "has_more": data.get("has_more", False),
        "page_token": data.get("page_token") or "",
    }
# endregion


# region MCP 工具实现
@ToolRegistry.register
class BitableListTablesTool(BaseTool):
    """获取多维表格表列表"""

    name = "feishu.v1.bitable.list_tables"
    description = "List Feishu bitable tables under an app token."
    parameters = {
        "type": "object",
        "properties": {
            "app_token": {"type": "string", "description": "Bitable app_token"},
            "refresh": {"type": "boolean", "description": "强制刷新缓存"},
        },
        "required": [],
    }

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        settings = self.context.settings
        app_token = params.get("app_token") or settings.bitable.default_app_token
        if not app_token:
            return {"tables": [], "total": 0}

        refresh = bool(params.get("refresh"))
        if refresh:
            _TABLES_CACHE.clear()
            _SCHEMA_CACHE.clear()

        tables = await _fetch_tables(self, app_token, refresh=refresh)
        return {"tables": tables, "total": len(tables)}


@ToolRegistry.register
class BitableSearchTool(BaseTool):
    """
    多维表格搜索工具

    功能:
        - 根据关键词、日期范围搜索记录
        - 支持字段筛选和自定义视图
    """
    name = "feishu.v1.bitable.search"
    description = "搜索飞书多维表格记录，支持关键词、日期范围、字段过滤"
    parameters = {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "搜索关键词",
            },
            "date_from": {
                "type": "string",
                "description": "开始日期 (YYYY-MM-DD)",
            },
            "date_to": {
                "type": "string",
                "description": "结束日期 (YYYY-MM-DD)",
            },
            "filters": {
                "type": "object",
                "description": "额外过滤条件",
            },
            "limit": {
                "type": "integer",
                "description": "返回数量限制",
                "default": 20,
            },
        },
        "required": [],
    }

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

        field_info = await _fetch_fields_info(self, app_token, table_id)
        field_names = set(field_info.keys())
        normalized_lookup = {
            _normalize_field_name(name): name for name in field_names
        }

        hearing_field = settings.bitable.field_mapping.get("hearing_date", "开庭日")
        if not field_names:
            hearing_field = ""
        if hearing_field:
            resolved = normalized_lookup.get(_normalize_field_name(hearing_field))
            if resolved:
                hearing_field = resolved
            elif field_names and hearing_field not in field_names:
                hearing_field = ""
        if not hearing_field and field_names:
            for name in field_names:
                if "开庭" in name or "庭审" in name:
                    hearing_field = name
                    break

        keyword_fields = settings.bitable.search.searchable_fields
        keyword_candidates = []
        if field_names:
            for field in keyword_fields:
                resolved = normalized_lookup.get(_normalize_field_name(field))
                if not resolved:
                    continue
                field_type = field_info.get(resolved, -1)
                if field_type == 1:
                    keyword_candidates.append(resolved)
        if not keyword_candidates and hearing_field:
            keyword_candidates = [hearing_field]

        keyword_conditions: list[dict[str, Any]] = []
        if keyword:
            for field in keyword_candidates:
                keyword_conditions.append(_build_keyword_condition(keyword, field))

        date_conditions: list[dict[str, Any]] = []
        date_filter_supported = False
        if hearing_field:
            field_type = field_info.get(hearing_field, -1)
            date_filter_supported = field_type in {5, 6, 7}
            if date_filter_supported:
                date_conditions = _build_date_conditions(hearing_field, date_from, date_to)
            elif field_type == 1 and (date_from or date_to):
                date_value = date_from or date_to
                if date_value:
                    date_conditions = [_build_keyword_condition(date_value, hearing_field)]
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

        limit = int(params.get("limit") or 100)
        limit = min(limit, settings.bitable.search.max_records)
        page_token = params.get("page_token")

        if field_names:
            field_names = _resolve_return_fields(
                field_names,
                normalized_lookup,
                settings,
                extra_fields=[hearing_field] if hearing_field else [],
            )
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
        if hearing_field and date_filter_supported:
            payload["sort"] = [{"field_name": hearing_field, "desc": False}]
        if page_token:
            payload["page_token"] = page_token

        result = await _search_records(self, app_token, table_id, view_id, payload)
        records = result.get("records", [])

        filtered = False
        if (date_from or date_to) and hearing_field and not date_filter_supported:
            start_date = _parse_date_text(date_from)
            end_date = _parse_date_text(date_to)
            filtered_records = []
            for record in records:
                value = record["fields_text"].get(hearing_field) or record["fields_text"].get(
                    _normalize_field_name(hearing_field)
                )
                record_date = _parse_date_text(value)
                if not record_date:
                    continue
                if start_date and record_date < start_date:
                    continue
                if end_date and record_date > end_date:
                    continue
                filtered_records.append(record)
            records = filtered_records
            filtered = True

        total = result.get("total") or len(records)
        if filtered:
            total = len(records)
        schema = _build_schema(field_info) if field_info else []
        return {
            "records": records,
            "total": total,
            "has_more": result.get("has_more", False),
            "page_token": result.get("page_token") or "",
            "schema": schema,
        }


@ToolRegistry.register
class BitableSearchExactTool(BaseTool):
    """精确匹配搜索工具"""

    name = "feishu.v1.bitable.search_exact"
    description = "Search a bitable record by exact field value."
    parameters = {
        "type": "object",
        "properties": {
            "app_token": {"type": "string", "description": "Bitable app_token"},
            "table_id": {"type": "string", "description": "数据表 table_id"},
            "view_id": {"type": "string", "description": "视图 view_id"},
            "field": {"type": "string", "description": "字段名"},
            "value": {"type": "string", "description": "字段值"},
            "limit": {"type": "integer", "description": "返回数量限制", "default": 100},
        },
        "required": ["field", "value"],
    }

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        settings = self.context.settings
        app_token = params.get("app_token") or settings.bitable.default_app_token
        table_id = params.get("table_id") or settings.bitable.default_table_id
        view_id = params.get("view_id") or settings.bitable.default_view_id
        field = params.get("field")
        value = params.get("value")

        if not app_token or not table_id or not field or value is None:
            return {"records": [], "total": 0}

        field_info = await _fetch_fields_info(self, app_token, table_id)
        field_names = set(field_info.keys())
        normalized_lookup = {
            _normalize_field_name(name): name for name in field_names
        }
        resolved_field = normalized_lookup.get(_normalize_field_name(field))
        if not resolved_field and field in field_names:
            resolved_field = field
        if not resolved_field:
            raise ValueError(f"Field not found: {field}")

        limit = int(params.get("limit") or 100)
        limit = min(limit, settings.bitable.search.max_records)

        field_names = _resolve_return_fields(
            field_names,
            normalized_lookup,
            settings,
            extra_fields=[resolved_field],
        )

        payload: dict[str, Any] = {
            "page_size": limit,
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {"field_name": resolved_field, "operator": "is", "value": [value]},
                ],
            },
        }
        if view_id:
            payload["view_id"] = view_id
        if field_names:
            payload["field_names"] = field_names

        result = await _search_records(self, app_token, table_id, view_id, payload)
        result["schema"] = _build_schema(field_info) if field_info else []
        return result


@ToolRegistry.register
class BitableSearchKeywordTool(BaseTool):
    """关键词搜索工具"""

    name = "feishu.v1.bitable.search_keyword"
    description = "Search bitable records by keyword across fields."
    parameters = {
        "type": "object",
        "properties": {
            "app_token": {"type": "string", "description": "Bitable app_token"},
            "table_id": {"type": "string", "description": "数据表 table_id"},
            "view_id": {"type": "string", "description": "视图 view_id"},
            "keyword": {"type": "string", "description": "搜索关键词"},
            "fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "关键词匹配字段列表 (可选)",
            },
            "limit": {"type": "integer", "description": "返回数量限制", "default": 100},
        },
        "required": ["keyword"],
    }

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        settings = self.context.settings
        app_token = params.get("app_token") or settings.bitable.default_app_token
        table_id = params.get("table_id") or settings.bitable.default_table_id
        view_id = params.get("view_id") or settings.bitable.default_view_id
        keyword = params.get("keyword")

        if not app_token or not table_id or not keyword:
            return {"records": [], "total": 0}

        field_info = await _fetch_fields_info(self, app_token, table_id)
        field_names = set(field_info.keys())
        normalized_lookup = {
            _normalize_field_name(name): name for name in field_names
        }

        candidates: list[str] = []
        for field in params.get("fields") or []:
            resolved = normalized_lookup.get(_normalize_field_name(field))
            if resolved:
                candidates.append(resolved)
        if not candidates and field_names:
            for field in settings.bitable.search.searchable_fields:
                resolved = normalized_lookup.get(_normalize_field_name(field))
                if not resolved:
                    continue
                if field_info.get(resolved) == 1:
                    candidates.append(resolved)
        if not candidates and field_names:
            candidates = [next(iter(field_names))]

        keyword_conditions = [_build_keyword_condition(keyword, field) for field in candidates]
        conjunction = "or" if len(keyword_conditions) > 1 else "and"

        limit = int(params.get("limit") or 100)
        limit = min(limit, settings.bitable.search.max_records)

        return_fields = _resolve_return_fields(
            field_names,
            normalized_lookup,
            settings,
            extra_fields=candidates,
        )

        payload: dict[str, Any] = {
            "page_size": limit,
            "filter": {
                "conjunction": conjunction,
                "conditions": keyword_conditions,
            },
        }
        if view_id:
            payload["view_id"] = view_id
        if return_fields:
            payload["field_names"] = return_fields

        result = await _search_records(self, app_token, table_id, view_id, payload)
        result["schema"] = _build_schema(field_info) if field_info else []
        return result


@ToolRegistry.register
class BitableSearchDateRangeTool(BaseTool):
    """日期范围搜索工具"""

    name = "feishu.v1.bitable.search_date_range"
    description = "Search bitable records by date range."
    parameters = {
        "type": "object",
        "properties": {
            "app_token": {"type": "string", "description": "Bitable app_token"},
            "table_id": {"type": "string", "description": "数据表 table_id"},
            "view_id": {"type": "string", "description": "视图 view_id"},
            "field": {"type": "string", "description": "日期字段"},
            "date_from": {"type": "string", "description": "开始日期 YYYY-MM-DD"},
            "date_to": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
            "limit": {"type": "integer", "description": "返回数量限制", "default": 100},
        },
        "required": ["field", "date_from", "date_to"],
    }

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        settings = self.context.settings
        app_token = params.get("app_token") or settings.bitable.default_app_token
        table_id = params.get("table_id") or settings.bitable.default_table_id
        view_id = params.get("view_id") or settings.bitable.default_view_id
        field = params.get("field")
        date_from = params.get("date_from")
        date_to = params.get("date_to")

        if not app_token or not table_id or not field:
            return {"records": [], "total": 0}

        field_info = await _fetch_fields_info(self, app_token, table_id)
        field_names = set(field_info.keys())
        normalized_lookup = {
            _normalize_field_name(name): name for name in field_names
        }
        resolved_field = normalized_lookup.get(_normalize_field_name(field))
        if not resolved_field and field in field_names:
            resolved_field = field
        if not resolved_field:
            resolved_field = next((name for name, ftype in field_info.items() if ftype in _DATE_FIELD_TYPES), None)
        if not resolved_field:
            raise ValueError("No date field available")
        if field_info.get(resolved_field) not in _DATE_FIELD_TYPES:
            raise ValueError(f"Field is not a date field: {resolved_field}")

        limit = int(params.get("limit") or 100)
        limit = min(limit, settings.bitable.search.max_records)

        return_fields = _resolve_return_fields(
            field_names,
            normalized_lookup,
            settings,
            extra_fields=[resolved_field],
        )

        payload: dict[str, Any] = {
            "page_size": limit,
            "filter": {
                "conjunction": "and",
                "conditions": _build_date_conditions(resolved_field, date_from, date_to),
            },
            "sort": [{"field_name": resolved_field, "desc": False}],
        }
        if view_id:
            payload["view_id"] = view_id
        if return_fields:
            payload["field_names"] = return_fields

        result = await _search_records(self, app_token, table_id, view_id, payload)
        result["schema"] = _build_schema(field_info) if field_info else []
        return result


@ToolRegistry.register
class BitableSearchAdvancedTool(BaseTool):
    """组合条件搜索工具"""

    name = "feishu.v1.bitable.search_advanced"
    description = "Search bitable records with multiple conditions (AND/OR)."
    parameters = {
        "type": "object",
        "properties": {
            "app_token": {"type": "string", "description": "Bitable app_token"},
            "table_id": {"type": "string", "description": "数据表 table_id"},
            "view_id": {"type": "string", "description": "视图 view_id"},
            "conditions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "op": {"type": "string"},
                        "value": {},
                    },
                },
            },
            "conjunction": {"type": "string", "description": "and/or", "default": "and"},
            "limit": {"type": "integer", "description": "返回数量限制", "default": 100},
        },
        "required": ["conditions"],
    }

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        settings = self.context.settings
        app_token = params.get("app_token") or settings.bitable.default_app_token
        table_id = params.get("table_id") or settings.bitable.default_table_id
        view_id = params.get("view_id") or settings.bitable.default_view_id
        conditions = params.get("conditions") or []

        if not app_token or not table_id or not conditions:
            return {"records": [], "total": 0}

        field_info = await _fetch_fields_info(self, app_token, table_id)
        field_names = set(field_info.keys())
        normalized_lookup = {
            _normalize_field_name(name): name for name in field_names
        }

        allowed_ops = {"is", "contains", "isGreater", "isLess", "isGreaterEqual", "isLessEqual"}
        parsed_conditions: list[dict[str, Any]] = []
        extra_fields: list[str] = []
        for item in conditions:
            field = item.get("field")
            op = item.get("op")
            value = item.get("value")
            if not field or not op:
                continue
            if op not in allowed_ops:
                raise ValueError(f"Unsupported operator: {op}")
            resolved_field = normalized_lookup.get(_normalize_field_name(field))
            if not resolved_field and field in field_names:
                resolved_field = field
            if not resolved_field:
                raise ValueError(f"Field not found: {field}")
            extra_fields.append(resolved_field)
            values = value if isinstance(value, list) else [value]
            parsed_conditions.append({
                "field_name": resolved_field,
                "operator": op,
                "value": values,
            })

        conjunction = (params.get("conjunction") or "and").lower()
        if conjunction not in {"and", "or"}:
            conjunction = "and"

        limit = int(params.get("limit") or 100)
        limit = min(limit, settings.bitable.search.max_records)

        return_fields = _resolve_return_fields(
            field_names,
            normalized_lookup,
            settings,
            extra_fields=extra_fields,
        )

        payload: dict[str, Any] = {
            "page_size": limit,
            "filter": {
                "conjunction": conjunction,
                "conditions": parsed_conditions,
            },
        }
        if view_id:
            payload["view_id"] = view_id
        if return_fields:
            payload["field_names"] = return_fields

        result = await _search_records(self, app_token, table_id, view_id, payload)
        result["schema"] = _build_schema(field_info) if field_info else []
        return result


@ToolRegistry.register
class BitableRecordGetTool(BaseTool):
    """
    获取记录详情工具

    功能:
        - 根据 record_id 获取单条记录完整信息
    """
    name = "feishu.v1.bitable.record.get"
    description = "Get a single bitable record by record_id."
    parameters = {
        "type": "object",
        "properties": {
            "record_id": {
                "type": "string",
                "description": "记录 ID",
            },
            "app_token": {
                "type": "string",
                "description": "多维表格 app_token (可选)",
            },
            "table_id": {
                "type": "string",
                "description": "数据表 table_id (可选)",
            },
        },
        "required": ["record_id"],
    }

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


@ToolRegistry.register
class BitableRecordCreateTool(BaseTool):
    """
    创建记录工具

    功能:
        - 在指定表格中创建新记录
        - 返回新记录的 ID 和链接
    """
    
    name = "feishu.v1.bitable.record.create"
    description = "Create a new bitable record with specified fields."

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        执行创建

        参数:
            params: 参数字典
                - fields: 字段值字典
                - app_token: 应用 Token (可选)
                - table_id: 数据表 ID (可选)

        返回:
            创建结果
        """
        fields = params.get("fields") or {}
        if not fields:
            return {"success": False, "error": "No fields provided"}

        settings = self.context.settings
        app_token = params.get("app_token") or settings.bitable.default_app_token
        table_id = params.get("table_id") or settings.bitable.default_table_id
        view_id = params.get("view_id") or settings.bitable.default_view_id

        if not app_token or not table_id:
            return {"success": False, "error": "Bitable not configured"}

        payload = {"fields": fields}

        response = await self.context.client.request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            json_body=payload,
        )
        data = response.get("data") or {}
        record = data.get("record") or {}
        record_id = record.get("record_id")

        record_url = ""
        if record_id:
            record_url = build_record_url(
                settings.bitable.domain,
                app_token,
                table_id,
                record_id,
                view_id=view_id,
            )

        return {
            "success": True,
            "record_id": record_id,
            "fields": record.get("fields", {}),
            "record_url": record_url,
        }


@ToolRegistry.register
class BitableRecordUpdateTool(BaseTool):
    """
    更新记录工具

    功能:
        - 更新指定记录的字段值
    """
    
    name = "feishu.v1.bitable.record.update"
    description = "Update an existing bitable record."

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        执行更新

        参数:
            params: 参数字典
                - record_id: 记录 ID
                - fields: 更新字段字典

        返回:
            更新结果
        """
        record_id = params.get("record_id")
        fields = params.get("fields") or {}
        
        if not record_id:
            return {"success": False, "error": "No record_id provided"}
        if not fields:
            return {"success": False, "error": "No fields to update"}

        settings = self.context.settings
        app_token = params.get("app_token") or settings.bitable.default_app_token
        table_id = params.get("table_id") or settings.bitable.default_table_id
        view_id = params.get("view_id") or settings.bitable.default_view_id

        if not app_token or not table_id:
            return {"success": False, "error": "Bitable not configured"}

        payload = {"fields": fields}

        response = await self.context.client.request(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            json_body=payload,
        )
        data = response.get("data") or {}
        record = data.get("record") or {}

        record_url = build_record_url(
            settings.bitable.domain,
            app_token,
            table_id,
            record_id,
            view_id=view_id,
        )

        return {
            "success": True,
            "record_id": record_id,
            "fields": record.get("fields", {}),
            "record_url": record_url,
        }


@ToolRegistry.register
class BitableRecordDeleteTool(BaseTool):
    """
    删除记录工具
    
    功能:
        - 删除指定的记录
    """
    
    name = "feishu.v1.bitable.record.delete"
    description = "Delete a bitable record by record_id."
    
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        执行删除
        
        参数:
            params: 参数字典
                - record_id: 记录 ID
                - app_token: 应用 Token (可选)
                - table_id: 数据表 ID (可选)
        
        返回:
            删除结果
        """
        record_id = params.get("record_id")
        
        if not record_id:
            return {"success": False, "error": "No record_id provided"}
        
        settings = self.context.settings
        app_token = params.get("app_token") or settings.bitable.default_app_token
        table_id = params.get("table_id") or settings.bitable.default_table_id
        
        if not app_token or not table_id:
            return {"success": False, "error": "Bitable not configured"}
        
        response = await self.context.client.request(
            "DELETE",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
        )
        
        # 删除成功通常返回空数据
        return {
            "success": True,
            "record_id": record_id,
            "message": "Record deleted successfully",
        }

# endregion
