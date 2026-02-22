"""
多表联动服务。

提供：
- 父表 Create/Update/Delete 后的子表联动写入
- 子表联动失败时的可修复任务描述
- 基于当前 active_record 的跨表查询联动参数重写
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from src.core.skills.data_writer import DataWriter, build_default_data_writer

logger = logging.getLogger(__name__)


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s\-_()（）\[\]【】、,，.:：/\\]+", "", text)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


class MultiTableLinker:
    """轻量联动引擎。"""

    _REFERENCE_TOKENS = ("这个", "这条", "那条", "上一条", "刚才", "刚刚", "第")

    def __init__(
        self,
        mcp_client: Any,
        skills_config: dict[str, Any] | None = None,
        data_writer: DataWriter | None = None,
    ) -> None:
        self._mcp = mcp_client
        self._data_writer = data_writer or build_default_data_writer(mcp_client)
        self._skills_config = skills_config or {}
        cfg = self._skills_config.get("multi_table") if isinstance(self._skills_config, dict) else {}
        self._enabled = bool((cfg or {}).get("enabled", False))
        self._links = self._normalize_links((cfg or {}).get("links"))

        self._tables_cache: list[dict[str, str]] = []
        self._tables_cache_at = 0.0
        self._tables_ttl_seconds = 120

    def _normalize_links(self, raw_links: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_links, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in raw_links:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip() or f"link_{len(normalized) + 1}"
            parent_tables = _as_list(item.get("parent_tables") or item.get("parent_table"))
            child_table = str(item.get("child_table") or item.get("child_table_name") or "").strip()
            child_table_id = str(item.get("child_table_id") or "").strip()
            if not child_table and not child_table_id:
                continue

            normalized.append(
                {
                    "name": name,
                    "enabled": bool(item.get("enabled", True)),
                    "parent_tables": parent_tables,
                    "child_table": child_table,
                    "child_table_id": child_table_id,
                    "parent_key": str(item.get("parent_key") or "案号").strip(),
                    "child_key": str(item.get("child_key") or item.get("parent_key") or "案号").strip(),
                    "create_fields": item.get("create_fields") if isinstance(item.get("create_fields"), dict) else {},
                    "update_fields": item.get("update_fields") if isinstance(item.get("update_fields"), dict) else {},
                    "create_if_missing_on_update": bool(item.get("create_if_missing_on_update", False)),
                    "delete_mode": str(item.get("delete_mode") or "none").strip().lower(),
                    "enable_create": bool(item.get("enable_create", True)),
                    "enable_update": bool(item.get("enable_update", True)),
                    "enable_delete": bool(item.get("enable_delete", False)),
                }
            )
        return normalized

    async def sync_after_create(
        self,
        *,
        parent_table_id: str | None,
        parent_table_name: str | None,
        parent_fields: dict[str, Any],
    ) -> dict[str, Any]:
        result = self._empty_result()
        if not self._enabled or not self._links:
            return result

        for link in self._links:
            if not link.get("enabled", True):
                continue
            if not link.get("enable_create", True):
                continue
            if not self._match_parent(link, parent_table_id, parent_table_name):
                continue

            child_table_id, child_table_name = await self._resolve_child_table(link)
            if not child_table_id:
                result["failures"].append(
                    self._build_failure(
                        link=link,
                        action="create",
                        error="未找到子表",
                        table_id=None,
                        table_name=link.get("child_table") or None,
                    )
                )
                continue

            parent_key = link.get("parent_key") or "案号"
            child_key = link.get("child_key") or parent_key
            join_value = self._pick_field_value(parent_fields, parent_key)

            child_fields = self._map_fields(parent_fields, link.get("create_fields") or {})
            if join_value is not None and str(join_value).strip():
                child_fields.setdefault(str(child_key), join_value)

            if not child_fields:
                continue

            try:
                create_result = await self._data_writer.create(
                    child_table_id,
                    child_fields,
                )
                if not create_result.success:
                    raise RuntimeError(str(create_result.error or "子表创建失败"))
                result["applied"] += 1
                result["success_count"] += 1
                result["successes"].append(
                    {
                        "link": link.get("name"),
                        "action": "create",
                        "table_id": child_table_id,
                        "table_name": child_table_name,
                        "record_id": create_result.record_id,
                    }
                )
            except Exception as exc:
                result["failures"].append(
                    self._build_failure(
                        link=link,
                        action="create",
                        error=str(exc),
                        table_id=child_table_id,
                        table_name=child_table_name,
                        retry_action="create",
                        retry_params={
                            "table_id": child_table_id,
                            "table_name": child_table_name,
                            "fields": child_fields,
                            "required_fields": list(child_fields.keys()),
                        },
                    )
                )

        return result

    async def sync_after_update(
        self,
        *,
        parent_table_id: str | None,
        parent_table_name: str | None,
        updated_fields: dict[str, Any],
        source_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self._empty_result()
        if not self._enabled or not self._links:
            return result

        source_fields = source_fields or {}
        for link in self._links:
            if not link.get("enabled", True):
                continue
            if not link.get("enable_update", True):
                continue
            if not self._match_parent(link, parent_table_id, parent_table_name):
                continue

            child_table_id, child_table_name = await self._resolve_child_table(link)
            if not child_table_id:
                result["failures"].append(
                    self._build_failure(
                        link=link,
                        action="update",
                        error="未找到子表",
                        table_id=None,
                        table_name=link.get("child_table") or None,
                    )
                )
                continue

            parent_key = link.get("parent_key") or "案号"
            child_key = link.get("child_key") or parent_key
            join_value = self._pick_field_value(updated_fields, parent_key)
            if join_value in (None, ""):
                join_value = self._pick_field_value(source_fields, parent_key)
            if join_value in (None, ""):
                continue

            mapped_updates = self._map_fields(updated_fields, link.get("update_fields") or {})
            if not mapped_updates:
                continue

            record_ids: list[str] = []
            try:
                search = await self._mcp.call_tool(
                    "feishu.v1.bitable.search_exact",
                    {
                        "table_id": child_table_id,
                        "field": child_key,
                        "value": join_value,
                    },
                )
                records = search.get("records") if isinstance(search.get("records"), list) else []
                if not records:
                    if link.get("create_if_missing_on_update", False):
                        fields_for_create = {**mapped_updates, str(child_key): join_value}
                        create_result = await self._data_writer.create(
                            child_table_id,
                            fields_for_create,
                        )
                        if not create_result.success:
                            raise RuntimeError(str(create_result.error or "子表创建失败"))
                        result["applied"] += 1
                        result["success_count"] += 1
                        result["successes"].append(
                            {
                                "link": link.get("name"),
                                "action": "create",
                                "table_id": child_table_id,
                                "table_name": child_table_name,
                                "record_id": create_result.record_id,
                            }
                        )
                    continue

                for record in records:
                    record_id = str(record.get("record_id") or "").strip()
                    if record_id:
                        record_ids.append(record_id)

                for record in records:
                    record_id = str(record.get("record_id") or "").strip()
                    if not record_id:
                        continue
                    update_result = await self._data_writer.update(
                        child_table_id,
                        record_id,
                        mapped_updates,
                    )
                    if not update_result.success:
                        raise RuntimeError(str(update_result.error or "子表更新失败"))
                    result["applied"] += 1
                    result["success_count"] += 1
                    result["successes"].append(
                        {
                            "link": link.get("name"),
                            "action": "update",
                            "table_id": child_table_id,
                            "table_name": child_table_name,
                            "record_id": record_id,
                        }
                    )
            except Exception as exc:
                result["failures"].append(
                    self._build_failure(
                        link=link,
                        action="update",
                        error=str(exc),
                        table_id=child_table_id,
                        table_name=child_table_name,
                        retry_action="update",
                        retry_params={
                            "table_id": child_table_id,
                            "table_name": child_table_name,
                            "record_ids": record_ids,
                            "match_field": child_key,
                            "match_value": join_value,
                            "fields": mapped_updates,
                            "required_fields": list(mapped_updates.keys()),
                        },
                    )
                )

        return result

    async def sync_after_delete(
        self,
        *,
        parent_table_id: str | None,
        parent_table_name: str | None,
        parent_fields: dict[str, Any],
    ) -> dict[str, Any]:
        result = self._empty_result()
        if not self._enabled or not self._links:
            return result

        for link in self._links:
            if not link.get("enabled", True):
                continue
            if not link.get("enable_delete", False):
                continue
            if link.get("delete_mode") != "delete":
                continue
            if not self._match_parent(link, parent_table_id, parent_table_name):
                continue

            child_table_id, child_table_name = await self._resolve_child_table(link)
            if not child_table_id:
                continue

            parent_key = link.get("parent_key") or "案号"
            child_key = link.get("child_key") or parent_key
            join_value = self._pick_field_value(parent_fields, parent_key)
            if join_value in (None, ""):
                continue

            try:
                search = await self._mcp.call_tool(
                    "feishu.v1.bitable.search_exact",
                    {
                        "table_id": child_table_id,
                        "field": child_key,
                        "value": join_value,
                    },
                )
                records = search.get("records") if isinstance(search.get("records"), list) else []
                for record in records:
                    record_id = str(record.get("record_id") or "").strip()
                    if not record_id:
                        continue
                    delete_result = await self._mcp.call_tool(
                        "feishu.v1.bitable.record.delete",
                        {
                            "table_id": child_table_id,
                            "record_id": record_id,
                        },
                    )
                    if not delete_result.get("success"):
                        raise RuntimeError(str(delete_result.get("error") or "子表删除失败"))
                    result["applied"] += 1
                    result["success_count"] += 1
                    result["successes"].append(
                        {
                            "link": link.get("name"),
                            "action": "delete",
                            "table_id": child_table_id,
                            "table_name": child_table_name,
                            "record_id": record_id,
                        }
                    )
            except Exception as exc:
                result["failures"].append(
                    self._build_failure(
                        link=link,
                        action="delete",
                        error=str(exc),
                        table_id=child_table_id,
                        table_name=child_table_name,
                    )
                )

        return result

    def summarize(self, sync_result: dict[str, Any]) -> str:
        success_count = int(sync_result.get("success_count") or 0)
        failures = sync_result.get("failures") if isinstance(sync_result.get("failures"), list) else []
        lines: list[str] = []
        if success_count > 0:
            lines.append(f"已同步 {success_count} 条关联表记录。")
        if failures:
            first = failures[0]
            table_name = str(first.get("table_name") or "子表")
            error = str(first.get("error") or "未知错误")
            lines.append(f"关联表「{table_name}」同步失败：{error}")
        return "\n".join(lines)

    def build_repair_pending(self, sync_result: dict[str, Any]) -> dict[str, Any] | None:
        raw_failures = sync_result.get("failures")
        if not isinstance(raw_failures, list):
            return None
        failures: list[dict[str, Any]] = [item for item in raw_failures if isinstance(item, dict)]
        for failure in failures:
            retry_action = str(failure.get("retry_action") or "").strip().lower()
            if retry_action not in {"create", "update"}:
                continue
            retry_params = failure.get("retry_params")
            if not isinstance(retry_params, dict):
                continue
            raw_fields = retry_params.get("fields")
            fields: dict[str, Any] = dict(raw_fields) if isinstance(raw_fields, dict) else {}
            required_fields_raw = retry_params.get("required_fields")
            required_fields = _as_list(required_fields_raw)
            if not required_fields:
                required_fields = [str(key) for key in fields.keys()]
            payload = {
                "table_id": retry_params.get("table_id"),
                "table_name": retry_params.get("table_name"),
                "fields": fields,
                "required_fields": required_fields,
                "awaiting_confirm": False,
                "awaiting_duplicate_confirm": False,
                "duplicate_checked": True,
                "skip_duplicate_check": True,
                "error": failure.get("error"),
            }
            if retry_action == "create":
                payload["repair_mode"] = "child_table_create"
                payload["repair_action"] = "repair_child_create"
                payload["auto_submit"] = True
            else:
                payload["repair_mode"] = "child_table_update"
                payload["repair_action"] = "repair_child_update"
                payload["auto_submit"] = True
                payload["record_ids"] = retry_params.get("record_ids")
                payload["match_field"] = retry_params.get("match_field")
                payload["match_value"] = retry_params.get("match_value")
            return payload
        return None

    def resolve_query_override(
        self,
        *,
        query: str,
        current_tool: str,
        params: dict[str, Any],
        target_table_id: str | None,
        target_table_name: str | None,
        active_table_id: str | None,
        active_table_name: str | None,
        active_record: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any]] | None:
        if not self._enabled or not self._links:
            return None
        if not active_record:
            return None
        if not self._is_reference_query(query):
            return None
        if current_tool == "feishu.v1.bitable.search_exact" and params.get("field") and params.get("value"):
            return None

        fields = active_record.get("fields_text") or active_record.get("fields") or {}
        if not isinstance(fields, dict):
            return None

        for link in self._links:
            if not link.get("enabled", True):
                continue
            if not self._match_parent(link, active_table_id, active_table_name):
                continue
            if not self._match_child(link, target_table_id, target_table_name):
                continue

            parent_key = link.get("parent_key") or "案号"
            child_key = link.get("child_key") or parent_key
            join_value = self._pick_field_value(fields, parent_key)
            if join_value in (None, ""):
                continue

            override_params = dict(params)
            override_params["field"] = child_key
            override_params["value"] = join_value
            return "feishu.v1.bitable.search_exact", override_params

        return None

    def _is_reference_query(self, query: str) -> bool:
        text = str(query or "").strip()
        if not text:
            return False
        return any(token in text for token in self._REFERENCE_TOKENS)

    def _empty_result(self) -> dict[str, Any]:
        return {
            "applied": 0,
            "success_count": 0,
            "successes": [],
            "failures": [],
        }

    def _build_failure(
        self,
        *,
        link: dict[str, Any],
        action: str,
        error: str,
        table_id: str | None,
        table_name: str | None,
        retry_action: str | None = None,
        retry_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "link": link.get("name"),
            "action": action,
            "error": error,
            "table_id": table_id,
            "table_name": table_name,
        }
        if retry_action:
            payload["retry_action"] = retry_action
        if retry_params:
            payload["retry_params"] = retry_params
        return payload

    async def _resolve_child_table(self, link: dict[str, Any]) -> tuple[str | None, str | None]:
        child_table_id = str(link.get("child_table_id") or "").strip()
        child_table_name = str(link.get("child_table") or "").strip()
        if child_table_id:
            if child_table_name:
                return child_table_id, child_table_name
            tables = await self._list_tables()
            for item in tables:
                if item.get("table_id") == child_table_id:
                    return child_table_id, item.get("table_name")
            return child_table_id, None

        if not child_table_name:
            return None, None
        tables = await self._list_tables()
        target_norm = _norm(child_table_name)
        for item in tables:
            name = str(item.get("table_name") or "")
            if _norm(name) == target_norm:
                return str(item.get("table_id") or "").strip() or None, name
        return None, child_table_name

    async def _list_tables(self) -> list[dict[str, str]]:
        now = time.time()
        if self._tables_cache and (now - self._tables_cache_at) < self._tables_ttl_seconds:
            return self._tables_cache

        try:
            result = await self._mcp.call_tool("feishu.v1.bitable.list_tables", {})
            tables_raw = result.get("tables") if isinstance(result.get("tables"), list) else []
            tables: list[dict[str, str]] = []
            for item in tables_raw:
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
            logger.warning("MultiTableLinker list tables failed: %s", exc)
            return self._tables_cache

    def _match_parent(self, link: dict[str, Any], table_id: str | None, table_name: str | None) -> bool:
        candidates = _as_list(link.get("parent_tables"))
        if not candidates:
            return True
        id_norm = _norm(table_id)
        name_norm = _norm(table_name)
        for candidate in candidates:
            c_norm = _norm(candidate)
            if not c_norm:
                continue
            if id_norm and c_norm == id_norm:
                return True
            if name_norm and c_norm == name_norm:
                return True
        return False

    def _match_child(self, link: dict[str, Any], table_id: str | None, table_name: str | None) -> bool:
        child_id = _norm(link.get("child_table_id"))
        child_name = _norm(link.get("child_table"))
        target_id = _norm(table_id)
        target_name = _norm(table_name)
        if child_id and target_id and child_id == target_id:
            return True
        if child_name and target_name and child_name == target_name:
            return True
        if child_id and target_name and child_id == target_name:
            return True
        return False

    def _pick_field_value(self, fields: dict[str, Any], field_name: str) -> Any:
        if not isinstance(fields, dict):
            return None
        if field_name in fields:
            return fields.get(field_name)

        target_norm = _norm(field_name)
        for key, value in fields.items():
            if _norm(key) == target_norm:
                return value
        return None

    def _map_fields(self, source: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(source, dict) or not isinstance(mapping, dict):
            return {}
        mapped: dict[str, Any] = {}
        for parent_field, child_field in mapping.items():
            parent_name = str(parent_field).strip()
            child_name = str(child_field).strip()
            if not parent_name or not child_name:
                continue
            value = self._pick_field_value(source, parent_name)
            if value is None:
                continue
            text = str(value).strip() if not isinstance(value, (dict, list)) else value
            if text in ("", [], {}):
                continue
            mapped[child_name] = value
        return mapped
