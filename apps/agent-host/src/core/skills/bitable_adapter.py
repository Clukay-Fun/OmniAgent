"""
描述: 多维表格上下文与字段适配器
主要功能:
    - 按查询/上下文动态解析目标表
    - 按表结构动态适配字段名
    - 统一处理 record_url 中 table_id 提取
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


def _normalize_text(value: str) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s\-_()（）\[\]【】、,，.:：/\\]+", "", text)


@dataclass
class TableContext:
    table_id: str | None = None
    table_name: str | None = None
    source: str = "unknown"


class BitableAdapter:
    """CRUD 技能共享的表/字段动态适配器。"""

    _FIELD_CANDIDATES: dict[str, list[str]] = {
        "律师": ["主办律师", "协办律师"],
        "主办律师": ["主办律师"],
        "协办律师": ["协办律师"],
        "委托人": ["委托人", "委托人及联系方式"],
        "委托人及联系方式": ["委托人", "委托人及联系方式"],
        "客户": ["委托人", "委托人及联系方式"],
        "联系人": ["联系人"],
        "联系方式": ["联系方式"],
        "对方": ["对方当事人"],
        "被告": ["对方当事人"],
        "原告": ["对方当事人"],
        "案号": ["案号"],
        "案由": ["案由"],
        "法院": ["审理法院"],
        "审理法院": ["审理法院"],
        "阶段": ["程序阶段", "审理程序"],
        "程序": ["程序阶段", "审理程序"],
        "程序阶段": ["程序阶段", "审理程序"],
        "审理程序": ["审理程序", "程序阶段"],
        "开庭": ["开庭日"],
        "开庭日": ["开庭日"],
        "法官": ["承办法官", "承办法官、助理及联系方式"],
        "承办法官": ["承办法官", "承办法官、助理及联系方式"],
        "承办法官、助理及联系方式": ["承办法官", "承办法官、助理及联系方式"],
        "进展": ["进展", "案件进展"],
        "案件进展": ["案件进展", "进展"],
        "待办": ["待做事项"],
        "待做事项": ["待做事项"],
        "备注": ["备注"],
        "项目id": ["项目ID"],
        "项目编号": ["项目ID"],
    }

    def __init__(self, mcp_client: Any, skills_config: dict[str, Any] | None = None) -> None:
        self._mcp = mcp_client
        self._skills_config = skills_config or {}

        self._table_alias_lookup = self._build_alias_lookup(self._skills_config.get("table_aliases") or {})
        self._field_candidates_by_norm = {
            _normalize_text(k): v[:] for k, v in self._FIELD_CANDIDATES.items()
        }

        self._tables_cache: list[dict[str, str]] = []
        self._tables_cache_at = 0.0
        self._tables_ttl_seconds = 120

        self._schema_cache: dict[str, tuple[float, list[str]]] = {}
        self._schema_ttl_seconds = 300

    def _build_alias_lookup(self, table_aliases: dict[str, Any]) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for table_name, aliases in table_aliases.items():
            names = [str(table_name)]
            if isinstance(aliases, list):
                names.extend([str(item) for item in aliases if item])
            for name in names:
                normalized = _normalize_text(name)
                if normalized:
                    lookup[normalized] = str(table_name)
        return lookup

    async def resolve_table_context(
        self,
        query: str,
        extra: dict[str, Any] | None,
        last_result: dict[str, Any] | None,
    ) -> TableContext:
        extra = extra or {}

        explicit = self._extract_from_extra(extra)
        if explicit.table_id or explicit.table_name:
            return await self._fill_table_name(explicit)

        from_last = self._extract_from_last_result(last_result)
        if from_last.table_id or from_last.table_name:
            return await self._fill_table_name(from_last)

        tables = await self._list_tables()
        if tables:
            matched = self._match_table_by_query(query, tables)
            if matched:
                return matched

        return TableContext()

    async def adapt_fields_for_table(
        self,
        fields: dict[str, Any],
        table_id: str | None,
    ) -> tuple[dict[str, Any], list[str], list[str]]:
        if not fields:
            return {}, [], []
        if not table_id:
            return dict(fields), [], []

        available_fields = await self.get_table_fields(table_id)
        if not available_fields:
            return dict(fields), [], []

        normalized_lookup = {_normalize_text(name): name for name in available_fields}
        adapted: dict[str, Any] = {}
        unresolved: list[str] = []

        for raw_key, value in fields.items():
            key = str(raw_key).strip()
            if not key:
                continue

            mapped = self._map_field_name(key, available_fields, normalized_lookup)
            if not mapped:
                unresolved.append(key)
                continue
            adapted[mapped] = value

        return adapted, unresolved, available_fields

    async def get_table_fields(self, table_id: str) -> list[str]:
        now = time.time()
        cached = self._schema_cache.get(table_id)
        if cached and now - cached[0] < self._schema_ttl_seconds:
            return cached[1]

        try:
            result = await self._mcp.call_tool(
                "feishu.v1.bitable.search",
                {
                    "table_id": table_id,
                    "ignore_default_view": True,
                    "limit": 1,
                },
            )
            schema = result.get("schema") or []
            fields = [str(item.get("name")) for item in schema if isinstance(item, dict) and item.get("name")]
            self._schema_cache[table_id] = (now, fields)
            return fields
        except Exception as exc:
            logger.warning("Get table schema failed (table_id=%s): %s", table_id, exc)
            return []

    async def search_exact_records(
        self,
        *,
        field: str,
        value: Any,
        table_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "field": field,
            "value": value,
        }
        if table_id:
            params["table_id"] = table_id
        try:
            result = await self._mcp.call_tool("feishu.v1.bitable.search_exact", params)
        except Exception as exc:
            logger.warning("Search exact failed (field=%s, table_id=%s): %s", field, table_id, exc)
            return []

        records = result.get("records") if isinstance(result, dict) else []
        if isinstance(records, list):
            return [item for item in records if isinstance(item, dict)]
        return []

    def build_field_not_found_message(
        self,
        unresolved: list[str],
        available_fields: list[str],
        table_name: str | None,
    ) -> str:
        missing = "、".join(unresolved[:5])
        samples = "、".join(available_fields[:10])
        table_label = f"表「{table_name}」" if table_name else "当前表"
        if samples:
            return f"{table_label}中找不到字段：{missing}。可用字段示例：{samples}"
        return f"{table_label}中找不到字段：{missing}。"

    def extract_table_id_from_record(self, record: dict[str, Any] | None) -> str | None:
        if not isinstance(record, dict):
            return None
        url = record.get("record_url")
        if not isinstance(url, str) or not url:
            return None
        try:
            parsed = urlparse(url)
            table = parse_qs(parsed.query).get("table", [""])[0]
            table = str(table).strip()
            return table or None
        except Exception:
            return None

    async def _list_tables(self, refresh: bool = False) -> list[dict[str, str]]:
        now = time.time()
        if not refresh and self._tables_cache and now - self._tables_cache_at < self._tables_ttl_seconds:
            return self._tables_cache

        try:
            result = await self._mcp.call_tool("feishu.v1.bitable.list_tables", {"refresh": bool(refresh)})
            items = result.get("tables") or []
            tables: list[dict[str, str]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                table_id = str(item.get("table_id") or "").strip()
                table_name = str(item.get("table_name") or "").strip()
                if not table_id:
                    continue
                tables.append({"table_id": table_id, "table_name": table_name})

            self._tables_cache = tables
            self._tables_cache_at = now
            return tables
        except Exception as exc:
            logger.warning("List tables failed: %s", exc)
            return self._tables_cache

    def _extract_from_extra(self, extra: dict[str, Any]) -> TableContext:
        table_id = str(extra.get("table_id") or "").strip() or None
        table_name = str(extra.get("table_name") or "").strip() or None

        planner = extra.get("planner_plan")
        if isinstance(planner, dict):
            params = planner.get("params")
            if isinstance(params, dict):
                if not table_id:
                    table_id = str(params.get("table_id") or "").strip() or None
                if not table_name:
                    table_name = str(params.get("table_name") or "").strip() or None

        return TableContext(table_id=table_id, table_name=table_name, source="extra")

    def _extract_from_last_result(self, last_result: dict[str, Any] | None) -> TableContext:
        if not isinstance(last_result, dict):
            return TableContext()

        table_id = str(last_result.get("table_id") or "").strip() or None
        table_name = str(last_result.get("table_name") or "").strip() or None

        pending = last_result.get("pending_delete")
        if isinstance(pending, dict):
            if not table_id:
                table_id = str(pending.get("table_id") or "").strip() or None
            if not table_name:
                table_name = str(pending.get("table_name") or "").strip() or None

        query_meta = last_result.get("query_meta")
        if isinstance(query_meta, dict):
            params = query_meta.get("params")
            if isinstance(params, dict):
                if not table_id:
                    table_id = str(params.get("table_id") or "").strip() or None

        if not table_id:
            records = last_result.get("records")
            if isinstance(records, list) and records:
                table_id = self.extract_table_id_from_record(records[0])

        return TableContext(table_id=table_id, table_name=table_name, source="last_result")

    async def _fill_table_name(self, context: TableContext) -> TableContext:
        if not context.table_id and not context.table_name:
            return context

        tables = await self._list_tables()
        if not tables:
            return context

        if context.table_id and not context.table_name:
            for item in tables:
                if item.get("table_id") == context.table_id:
                    context.table_name = item.get("table_name")
                    return context

        if context.table_name and not context.table_id:
            for item in tables:
                if item.get("table_name") == context.table_name:
                    context.table_id = item.get("table_id")
                    return context

        return context

    def _match_table_by_query(self, query: str, tables: list[dict[str, str]]) -> TableContext | None:
        if not query or not tables:
            return None

        query_norm = _normalize_text(query)
        if not query_norm:
            return None

        by_name = {str(item.get("table_name") or ""): str(item.get("table_id") or "") for item in tables}
        for table_name, table_id in by_name.items():
            if table_name and _normalize_text(table_name) in query_norm:
                return TableContext(table_id=table_id or None, table_name=table_name, source="query_table_name")

        for alias_norm, table_name in self._table_alias_lookup.items():
            if alias_norm and alias_norm in query_norm and table_name in by_name:
                return TableContext(
                    table_id=by_name.get(table_name) or None,
                    table_name=table_name,
                    source="query_alias",
                )

        return None

    def _map_field_name(
        self,
        input_name: str,
        available_fields: list[str],
        normalized_lookup: dict[str, str],
    ) -> str | None:
        if input_name in available_fields:
            return input_name

        normalized = _normalize_text(input_name)
        if normalized and normalized in normalized_lookup:
            return normalized_lookup[normalized]

        for candidate in self._candidate_field_names(input_name):
            if candidate in available_fields:
                return candidate
            candidate_norm = _normalize_text(candidate)
            if candidate_norm in normalized_lookup:
                return normalized_lookup[candidate_norm]

        fuzzy = []
        for field in available_fields:
            fn = _normalize_text(field)
            if not normalized or not fn:
                continue
            if normalized in fn or fn in normalized:
                fuzzy.append(field)
        if len(fuzzy) == 1:
            return fuzzy[0]

        return None

    def _candidate_field_names(self, input_name: str) -> list[str]:
        candidates: list[str] = [input_name]
        normalized = _normalize_text(input_name)
        if normalized in self._field_candidates_by_norm:
            candidates.extend(self._field_candidates_by_norm[normalized])

        seen: set[str] = set()
        ordered: list[str] = []
        for item in candidates:
            key = str(item).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(key)
        return ordered
