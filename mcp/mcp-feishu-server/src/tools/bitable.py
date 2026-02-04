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
from src.utils.url_builder import build_record_url


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


async def _fetch_fields_info(tool: "BitableSearchTool", app_token: str, table_id: str) -> dict[str, int]:
    """获取数据表字段定义元数据"""
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
        return fields
    except Exception:
        return {}


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
# endregion


# region MCP 工具实现
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
        if hearing_field and date_filter_supported:
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

        total = data.get("total") or len(records)
        if filtered:
            total = len(records)
        return {
            "records": records,
            "total": total,
            "has_more": data.get("has_more", False),
            "page_token": data.get("page_token") or "",
        }


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

# endregion
