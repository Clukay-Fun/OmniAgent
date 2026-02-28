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
import os
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

def _normalize_text(value: str) -> str:
    """
    规范化文本，去除多余空格、特殊字符并转换为小写。

    功能:
        - 去除字符串前后的空格
        - 将字符串转换为小写
        - 使用正则表达式去除特殊字符
    """
    text = str(value or "").strip().lower()
    return re.sub(r"[\s\-_()（）\[\]【】、,，.:：/\\]+", "", text)

@dataclass
class TableContext:
    """
    表格上下文数据类，包含表ID、表名、应用令牌和来源信息。
    """
    table_id: str | None = None
    table_name: str | None = None
    app_token: str | None = None
    source: str = "unknown"

class BitableAdapter:
    """
    CRUD 技能共享的表/字段动态适配器。

    功能:
        - 提供表格和字段的动态解析和适配功能
        - 支持从查询、上下文、额外信息中解析表格上下文
        - 提供字段名的动态适配功能
        - 提供表格字段的缓存和查询功能
    """

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
        """
        初始化 BitableAdapter。

        功能:
            - 初始化 MCP 客户端和技能配置
            - 解析默认应用令牌
            - 构建表别名查找表
            - 初始化字段候选名称的规范化查找表
            - 初始化表格缓存和字段模式缓存
        """
        self._mcp = mcp_client
        self._skills_config = skills_config or {}
        self._default_app_token = self._resolve_default_app_token()

        self._table_alias_lookup = self._build_alias_lookup(self._skills_config.get("table_aliases") or {})
        self._field_candidates_by_norm = {
            _normalize_text(k): v[:] for k, v in self._FIELD_CANDIDATES.items()
        }

        self._tables_cache: list[dict[str, str]] = []
        self._tables_cache_at = 0.0
        self._tables_ttl_seconds = 120

        self._schema_cache: dict[str, tuple[float, list[str]]] = {}
        self._schema_ttl_seconds = 300

    def _resolve_default_app_token(self) -> str | None:
        """
        解析默认应用令牌。

        功能:
            - 从环境变量中解析默认应用令牌
        """
        for key in ("BITABLE_APP_TOKEN", "FEISHU_BITABLE_APP_TOKEN", "APP_TOKEN"):
            value = str(os.getenv(key) or "").strip()
            if value:
                return value
        return None

    def _apply_default_app_token(self, context: TableContext) -> TableContext:
        """
        应用默认应用令牌到表格上下文。

        功能:
            - 如果上下文中没有应用令牌，则应用默认应用令牌
        """
        if context.app_token:
            return context
        if self._default_app_token:
            context.app_token = self._default_app_token
        return context

    def _build_alias_lookup(self, table_aliases: dict[str, Any]) -> dict[str, str]:
        """
        构建表别名查找表。

        功能:
            - 从表别名配置中构建规范化名称到表名的查找表
        """
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
        """
        解析表格上下文。

        功能:
            - 从额外信息、上次结果和查询中解析表格上下文
        """
        extra = extra or {}

        explicit = self._extract_from_extra(extra)
        if explicit.table_id or explicit.table_name:
            resolved = await self._fill_table_name(explicit)
            return self._apply_default_app_token(resolved)

        from_last = self._extract_from_last_result(last_result)
        if from_last.table_id or from_last.table_name:
            resolved = await self._fill_table_name(from_last)
            return self._apply_default_app_token(resolved)

        tables = await self._list_tables()
        if tables:
            matched = self._match_table_by_query(query, tables)
            if matched:
                return self._apply_default_app_token(matched)

        return self._apply_default_app_token(TableContext())

    async def adapt_fields_for_table(
        self,
        fields: dict[str, Any],
        table_id: str | None,
    ) -> tuple[dict[str, Any], list[str], list[str]]:
        """
        为指定表格适配字段。

        功能:
            - 根据表格ID适配字段名称
            - 返回适配后的字段、未解析的字段和可用字段
        """
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
        """
        获取指定表格的字段列表。

        功能:
            - 从缓存中获取字段列表，如果缓存过期则从API获取
        """
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

    async def get_fields(self, table_id: str) -> list[str]:
        """
        向后兼容的 get_table_fields 别名。

        功能:
            - 调用 get_table_fields 方法
        """
        return await self.get_table_fields(table_id)

    async def search_exact_records(
        self,
        *,
        field: str,
        value: Any,
        table_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        搜索精确匹配的记录。

        功能:
            - 调用 API 搜索精确匹配的记录
        """
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
        """
        构建字段未找到的消息。

        功能:
            - 根据未解析的字段和可用字段构建消息
        """
        missing = "、".join(unresolved[:5])
        samples = "、".join(available_fields[:10])
        table_label = f"表「{table_name}」" if table_name else "当前表"
        if samples:
            return f"{table_label}中找不到字段：{missing}。可用字段示例：{samples}"
        return f"{table_label}中找不到字段：{missing}。"

    def extract_table_id_from_record(self, record: dict[str, Any] | None) -> str | None:
        """
        从记录中提取表格ID。

        功能:
            - 从记录中提取 table_id 或从 record_url 中解析 table_id
        """
        if not isinstance(record, dict):
            return None
        direct_table_id = str(record.get("table_id") or "").strip()
        if direct_table_id:
            return direct_table_id
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
        """
        列出所有表格。

        功能:
            - 从缓存中获取表格列表，如果缓存过期或需要刷新则从API获取
        """
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
        """
        从额外信息中提取表格上下文。

        功能:
            - 从 extra 中提取 table_id, table_name, app_token
        """
        table_id = str(extra.get("table_id") or extra.get("active_table_id") or "").strip() or None
        table_name = str(extra.get("table_name") or extra.get("active_table_name") or "").strip() or None
        app_token = str(extra.get("app_token") or extra.get("active_app_token") or "").strip() or None

        active_record = extra.get("active_record")
        if isinstance(active_record, dict):
            if not table_id:
                table_id = str(active_record.get("table_id") or "").strip() or None
            if not table_name:
                table_name = str(active_record.get("table_name") or "").strip() or None
            if not app_token:
                app_token = str(active_record.get("app_token") or "").strip() or None

        pending = extra.get("pending_action")
        if isinstance(pending, dict):
            payload = pending.get("payload")
            if isinstance(payload, dict):
                if not table_id:
                    table_id = str(payload.get("table_id") or "").strip() or None
                if not table_name:
                    table_name = str(payload.get("table_name") or "").strip() or None
                if not app_token:
                    app_token = str(payload.get("app_token") or "").strip() or None

        planner = extra.get("planner_plan")
        if isinstance(planner, dict):
            params = planner.get("params")
            if isinstance(params, dict):
                if not table_id:
                    table_id = str(params.get("table_id") or "").strip() or None
                if not table_name:
                    table_name = str(params.get("table_name") or "").strip() or None
                if not app_token:
                    app_token = str(params.get("app_token") or "").strip() or None

        return TableContext(table_id=table_id, table_name=table_name, app_token=app_token, source="extra")

    def _extract_from_last_result(self, last_result: dict[str, Any] | None) -> TableContext:
        """
        从上次结果中提取表格上下文。

        功能:
            - 从 last_result 中提取 table_id, table_name, app_token
        """
        if not isinstance(last_result, dict):
            return TableContext()

        table_id = str(last_result.get("table_id") or "").strip() or None
        table_name = str(last_result.get("table_name") or "").strip() or None
        app_token = str(last_result.get("app_token") or "").strip() or None

        pending = last_result.get("pending_delete")
        if isinstance(pending, dict):
            if not table_id:
                table_id = str(pending.get("table_id") or "").strip() or None
            if not table_name:
                table_name = str(pending.get("table_name") or "").strip() or None
            if not app_token:
                app_token = str(pending.get("app_token") or "").strip() or None

        query_meta = last_result.get("query_meta")
        if isinstance(query_meta, dict):
            params = query_meta.get("params")
            if isinstance(params, dict):
                if not table_id:
                    table_id = str(params.get("table_id") or "").strip() or None
                if not app_token:
                    app_token = str(params.get("app_token") or "").strip() or None

        if not table_id:
            records = last_result.get("records")
            if isinstance(records, list) and records:
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    record_table_id = self.extract_table_id_from_record(record)
                    if not record_table_id:
                        continue
                    table_id = record_table_id
                    if not app_token:
                        app_token = str(record.get("app_token") or "").strip() or None
                    break

        return TableContext(table_id=table_id, table_name=table_name, app_token=app_token, source="last_result")

    async def _fill_table_name(self, context: TableContext) -> TableContext:
        """
        填充表格名称。

        功能:
            - 根据 table_id 或 table_name 填充缺失的信息
        """
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
        """
        根据查询匹配表格。

        功能:
            - 根据查询字符串匹配表格
        """
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
        """
        映射字段名称。

        功能:
            - 根据可用字段和规范化查找表映射字段名称
        """
        # Step 1: exact match in schema
        if input_name in available_fields:
            return input_name

        # Keep historical alias candidates, but only for exact-like checks
        candidates = self._candidate_field_names(input_name)
        for candidate in candidates:
            if candidate in available_fields:
                return candidate

        # Step 1.5: exact match after text normalization
        for candidate in candidates:
            normalized_candidate = _normalize_text(candidate)
            if not normalized_candidate:
                continue
            matched = normalized_lookup.get(normalized_candidate)
            if matched:
                return matched

        # Step 2: exact match after removing spaces
        compact_lookup: dict[str, list[str]] = {}
        for field in available_fields:
            compact = re.sub(r"\s+", "", str(field))
            if not compact:
                continue
            compact_lookup.setdefault(compact, []).append(field)

        for candidate in candidates:
            compact_candidate = re.sub(r"\s+", "", str(candidate))
            if not compact_candidate:
                continue
            compact_matches = compact_lookup.get(compact_candidate, [])
            if len(compact_matches) == 1:
                return compact_matches[0]
            if len(compact_matches) >= 2:
                return None

        normalized = _normalize_text(input_name)
        if not normalized:
            return None

        # Step 3: suffix match ("状态" -> "任务状态"), only unique match is accepted
        suffix_matches: list[str] = []
        for field in available_fields:
            fn = _normalize_text(field)
            if fn and fn.endswith(normalized):
                suffix_matches.append(field)
        if len(suffix_matches) == 1:
            return suffix_matches[0]
        if len(suffix_matches) >= 2:
            return None

        # Step 4: contains match ("相关" -> "相关人"), only unique match is accepted
        contains_matches: list[str] = []
        for field in available_fields:
            fn = _normalize_text(field)
            if fn and normalized in fn:
                contains_matches.append(field)
        if len(contains_matches) == 1:
            return contains_matches[0]

        # Step 5: multiple candidates are treated as ambiguous; require user clarification
        return None

    def _candidate_field_names(self, input_name: str) -> list[str]:
        """
        获取字段名称的候选列表。

        功能:
            - 根据输入名称获取字段名称的候选列表
        """
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
