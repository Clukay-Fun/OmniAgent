"""
描述: 自动化服务处理侧 mixin。
主要功能:
    - 记录变更处理与规则触发
    - 快照初始化、扫描补偿与删除对账
    - Schema 刷新编排
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, cast

from src.automation.models import (
    AutomationValidationError,
    _is_watch_mode_full,
    _normalize_record_id,
    _to_int_timestamp,
    _watch_fields,
)
from src.automation.rules import build_business_hash_payload
from src.feishu.client import FeishuAPIError


LOGGER = logging.getLogger(__name__)


class AutomationProcessorMixin:
    def _build_business_key(self, table_id: str, record_id: str, diff: dict[str, Any]) -> str:
        changed = diff.get("changed") or {}
        if not isinstance(changed, dict):
            changed = {}
        encoded = build_business_hash_payload(table_id, record_id, changed)
        digest = hashlib.sha1(encoded.encode("utf-8")).hexdigest()
        return f"{table_id}:{record_id}:{digest}"

    async def _fetch_record_fields(self, app_token: str, table_id: str, record_id: str) -> dict[str, Any]:
        return await self._fetch_record_fields_with_watch(app_token, table_id, record_id, field_names=None)

    async def _fetch_record_fields_with_watch(
        self,
        app_token: str,
        table_id: str,
        record_id: str,
        field_names: list[str] | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] | None = None
        if field_names:
            params = {
                "field_names": json.dumps(field_names, ensure_ascii=False),
            }

        try:
            response = await self._client.request(
                "GET",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
                params=params,
            )
        except FeishuAPIError:
            if not params:
                raise
            response = await self._client.request(
                "GET",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            )

        data = response.get("data") or {}
        record = data.get("record") or data
        fields = record.get("fields")
        if not isinstance(fields, dict):
            raise AutomationValidationError("record fields missing in API response")
        return fields

    @staticmethod
    def _filter_fields_by_watch(fields: dict[str, Any], watch_plan: dict[str, Any]) -> dict[str, Any]:
        if _is_watch_mode_full(watch_plan):
            return dict(fields)

        watched = set(_watch_fields(watch_plan))
        if not watched:
            return dict(fields)

        filtered: dict[str, Any] = {}
        for key, value in fields.items():
            if key in watched:
                filtered[key] = value
        return filtered

    async def _list_records_page(
        self,
        app_token: str,
        table_id: str,
        page_token: str,
        page_size: int,
        field_names: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "page_size": page_size,
        }
        if page_token:
            payload["page_token"] = page_token
        if field_names:
            payload["field_names"] = field_names
        response = await self._client.request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
            json_body=payload,
        )
        data = response.get("data") or {}
        items = data.get("items") or []
        if not isinstance(items, list):
            items = []
        return {
            "items": items,
            "has_more": bool(data.get("has_more")),
            "page_token": str(data.get("page_token") or ""),
        }

    @staticmethod
    def _extract_record_meta(item: dict[str, Any]) -> tuple[str, dict[str, Any], int]:
        record_id = _normalize_record_id(item.get("record_id") or item.get("recordId") or item.get("id"))
        raw_fields = item.get("fields")
        fields = cast(dict[str, Any], raw_fields) if isinstance(raw_fields, dict) else {}
        modified_time = _to_int_timestamp(
            item.get("last_modified_time")
            or item.get("lastModifiedTime")
            or item.get("last_modified_timestamp")
            or item.get("lastModifiedTimestamp")
            or item.get("modified_time")
        )
        return record_id, fields, modified_time

    async def handle_record_changed(
        self,
        event_id: str,
        app_token: str,
        table_id: str,
        record_id: str,
        trigger_on_new_record: bool | None = None,
        notify_target: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if trigger_on_new_record is None:
            trigger_on_new_record = bool(self._settings.automation.trigger_on_new_record_event)

        watch_plan = self._engine.get_watch_plan(table_id, app_token=app_token)
        watch_fields = None if _is_watch_mode_full(watch_plan) else _watch_fields(watch_plan)
        current_raw = await self._fetch_record_fields_with_watch(
            app_token,
            table_id,
            record_id,
            field_names=watch_fields,
        )
        current_fields = self._filter_fields_by_watch(current_raw, watch_plan)
        return await self._process_record_changed(
            event_id,
            app_token,
            table_id,
            record_id,
            current_fields,
            trigger_on_new_record=bool(trigger_on_new_record),
            notify_target=notify_target,
        )

    async def _process_record_changed(
        self,
        event_id: str,
        app_token: str,
        table_id: str,
        record_id: str,
        current_fields: dict[str, Any],
        trigger_on_new_record: bool,
        notify_target: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        old_fields = self._snapshot.load(table_id, record_id)
        normalized_notify_target = self._coerce_notify_target(
            notify_target if isinstance(notify_target, dict) else {}
        )
        extra_context = {"notify_target": normalized_notify_target} if normalized_notify_target else None

        if old_fields is None:
            if not trigger_on_new_record:
                self._snapshot.save(table_id, record_id, current_fields)
                return {
                    "kind": "initialized",
                    "event_id": event_id,
                    "table_id": table_id,
                    "record_id": record_id,
                }

            diff = self._snapshot.diff({}, current_fields)
            if not diff.get("has_changes"):
                self._snapshot.save(table_id, record_id, current_fields)
                return {
                    "kind": "initialized",
                    "event_id": event_id,
                    "table_id": table_id,
                    "record_id": record_id,
                }

            business_key = self._build_business_key(table_id, record_id, diff)
            if self._idempotency.is_business_duplicate(business_key):
                self._snapshot.save(table_id, record_id, current_fields)
                return {
                    "kind": "duplicate_business",
                    "event_id": event_id,
                    "table_id": table_id,
                    "record_id": record_id,
                    "business_key": business_key,
                }

            self._idempotency.mark_business(business_key)
            rule_execution = await self._engine.execute(
                app_token=app_token,
                table_id=table_id,
                record_id=record_id,
                event_id=event_id,
                old_fields={},
                current_fields=current_fields,
                diff=diff,
                event_kind="created",
                extra_context=extra_context,
            )
            await self._notify_rule_execution_results(
                app_token=app_token,
                table_id=table_id,
                record_id=record_id,
                event_kind="created",
                rule_execution=rule_execution,
                inherited_notify_target=normalized_notify_target,
            )
            self._snapshot.save(table_id, record_id, current_fields)
            changed = diff.get("changed") or {}
            return {
                "kind": "initialized_triggered",
                "event_id": event_id,
                "table_id": table_id,
                "record_id": record_id,
                "changed_fields": sorted(changed.keys()) if isinstance(changed, dict) else [],
                "business_key": business_key,
                "rules": rule_execution,
            }

        diff = self._snapshot.diff(old_fields, current_fields)
        if not diff.get("has_changes"):
            self._snapshot.save(table_id, record_id, current_fields)
            return {
                "kind": "no_change",
                "event_id": event_id,
                "table_id": table_id,
                "record_id": record_id,
            }

        business_key = self._build_business_key(table_id, record_id, diff)
        if self._idempotency.is_business_duplicate(business_key):
            self._snapshot.save(table_id, record_id, current_fields)
            return {
                "kind": "duplicate_business",
                "event_id": event_id,
                "table_id": table_id,
                "record_id": record_id,
                "business_key": business_key,
            }

        self._idempotency.mark_business(business_key)
        rule_execution = await self._engine.execute(
            app_token=app_token,
            table_id=table_id,
            record_id=record_id,
            event_id=event_id,
            old_fields=old_fields,
            current_fields=current_fields,
            diff=diff,
            event_kind="updated",
            extra_context=extra_context,
        )
        await self._notify_rule_execution_results(
            app_token=app_token,
            table_id=table_id,
            record_id=record_id,
            event_kind="updated",
            rule_execution=rule_execution,
            inherited_notify_target=normalized_notify_target,
        )
        self._snapshot.save(table_id, record_id, current_fields)
        changed = diff.get("changed") or {}
        return {
            "kind": "changed",
            "event_id": event_id,
            "table_id": table_id,
            "record_id": record_id,
            "changed_fields": sorted(changed.keys()) if isinstance(changed, dict) else [],
            "business_key": business_key,
            "rules": rule_execution,
        }

    def _resolve_table_params(self, table_id: str | None, app_token: str | None) -> tuple[str, str]:
        resolved_table_id = str(table_id or self._settings.bitable.default_table_id or "").strip()
        resolved_app_token = str(app_token or self._settings.bitable.default_app_token or "").strip()
        if not resolved_table_id or not resolved_app_token:
            raise AutomationValidationError("table_id/app_token required")
        return resolved_table_id, resolved_app_token

    def get_poll_table_ids(self) -> list[str]:
        ids: list[str] = []
        for target in self.get_poll_targets():
            table_id = target.get("table_id")
            if table_id and table_id not in ids:
                ids.append(table_id)
        return ids

    def get_poll_targets(self) -> list[dict[str, str]]:
        default_table_id = str(self._settings.bitable.default_table_id or "").strip()
        default_app_token = str(self._settings.bitable.default_app_token or "").strip()
        return self._engine.list_poll_targets(default_table_id, default_app_token)

    async def refresh_schema_table(self, table_id: str, app_token: str, triggered_by: str) -> dict[str, Any]:
        if self._schema_watcher is None:
            return {
                "status": "disabled",
                "table_id": table_id,
                "app_token": app_token,
                "reason": "schema_sync_disabled",
            }
        return await self._schema_watcher.refresh_table(app_token=app_token, table_id=table_id, triggered_by=triggered_by)

    async def refresh_schema_once_all_tables(self, triggered_by: str = "manual") -> dict[str, Any]:
        if self._schema_watcher is None:
            return {
                "status": "disabled",
                "mode": "schema_refresh",
                "results": [],
                "reason": "schema_sync_disabled",
            }

        targets = self.get_poll_targets()
        results: list[dict[str, Any]] = []
        for target in targets:
            table_id = str(target.get("table_id") or "").strip()
            app_token = str(target.get("app_token") or "").strip()
            if not table_id or not app_token:
                continue
            try:
                result = await self.refresh_schema_table(table_id=table_id, app_token=app_token, triggered_by=triggered_by)
                results.append(result)
            except FeishuAPIError as exc:
                LOGGER.exception("schema refresh failed for table %s: %s", table_id, exc)
                results.append(
                    {
                        "status": "failed",
                        "table_id": table_id,
                        "app_token": app_token,
                        "error": self._format_error(exc),
                    }
                )
            except Exception as exc:
                LOGGER.exception("schema refresh failed for table %s: %s", table_id, exc)
                results.append(
                    {
                        "status": "failed",
                        "table_id": table_id,
                        "app_token": app_token,
                        "error": self._format_error(exc),
                    }
                )

        return {
            "status": "ok",
            "mode": "schema_refresh",
            "triggered_by": triggered_by,
            "tables": [target.get("table_id") for target in targets],
            "results": results,
        }

    async def trigger_schema_webhook_drill(
        self,
        *,
        table_id: str,
        app_token: str,
        triggered_by: str = "manual",
    ) -> dict[str, Any]:
        if self._schema_watcher is None:
            return {
                "status": "disabled",
                "reason": "schema_sync_disabled",
                "table_id": table_id,
                "app_token": app_token,
            }
        if not bool(self._settings.automation.schema_webhook_drill_enabled):
            return {
                "status": "disabled",
                "reason": "schema_webhook_drill_disabled",
                "table_id": table_id,
                "app_token": app_token,
            }

        result = await self._schema_watcher.send_risk_drill(
            app_token=app_token,
            table_id=table_id,
            triggered_by=triggered_by,
        )
        return {
            "status": "ok" if result.get("status") == "sent" else "failed",
            "table_id": table_id,
            "app_token": app_token,
            "drill": True,
            "webhook": result,
        }

    async def init_snapshot(self, table_id: str | None = None, app_token: str | None = None) -> dict[str, Any]:
        self._ensure_enabled()
        resolved_table_id, resolved_app_token = self._resolve_table_params(table_id, app_token)

        watch_plan = self._engine.get_watch_plan(resolved_table_id, app_token=resolved_app_token)
        watch_fields = None if _is_watch_mode_full(watch_plan) else _watch_fields(watch_plan)

        page_token = ""
        page_size = max(1, min(self._settings.automation.scan_page_size, 500))
        max_pages = max(1, self._settings.automation.max_scan_pages)

        records: dict[str, dict[str, Any]] = {}
        max_cursor = 0
        pages = 0

        while pages < max_pages:
            page = await self._list_records_page(
                resolved_app_token,
                resolved_table_id,
                page_token,
                page_size,
                field_names=watch_fields,
            )
            pages += 1
            items = page.get("items") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                record_id, fields, modified_time = self._extract_record_meta(item)
                if not record_id:
                    continue
                records[record_id] = self._filter_fields_by_watch(fields, watch_plan)
                if modified_time > max_cursor:
                    max_cursor = modified_time

            if not page.get("has_more"):
                break
            page_token = str(page.get("page_token") or "")
            if not page_token:
                break

        count = self._snapshot.init_full_snapshot(resolved_table_id, records)
        if max_cursor > 0:
            self._checkpoint.set(resolved_table_id, max_cursor)

        return {
            "status": "ok",
            "table_id": resolved_table_id,
            "records": count,
            "pages": pages,
            "cursor": max_cursor,
            "mode": "initialized",
        }

    @staticmethod
    def _to_scalar_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, dict):
            for key in ("text", "id", "name"):
                candidate = value.get(key)
                if isinstance(candidate, (str, int, float, bool)):
                    return str(candidate).strip()
            nested = value.get("value")
            return AutomationProcessorMixin._to_scalar_text(nested)
        if isinstance(value, list):
            for item in value:
                normalized = AutomationProcessorMixin._to_scalar_text(item)
                if normalized:
                    return normalized
            return ""
        return str(value).strip()

    def _extract_sync_delete_targets(self, source_table_id: str, source_app_token: str) -> list[dict[str, str]]:
        rules = self._engine.rule_store.load_enabled_rules(source_table_id, app_token=source_app_token)
        targets: list[dict[str, str]] = []
        seen: set[tuple[str, str, str, str]] = set()

        for rule in rules:
            pipeline = rule.get("pipeline") or {}
            if not isinstance(pipeline, dict):
                continue
            actions = pipeline.get("actions") or []
            if not isinstance(actions, list):
                continue

            for action in actions:
                if not isinstance(action, dict):
                    continue
                if str(action.get("type") or "").strip() != "bitable.upsert":
                    continue

                target_table_id = str(action.get("target_table_id") or "").strip()
                target_app_token = str(action.get("target_app_token") or source_app_token or "").strip()
                if not target_table_id or not target_app_token:
                    continue

                match_fields = action.get("match_fields")
                if not isinstance(match_fields, dict):
                    continue

                source_record_field = ""
                for field_name, template in match_fields.items():
                    if str(template or "").strip() == "{record_id}":
                        source_record_field = str(field_name or "").strip()
                        break
                if not source_record_field:
                    continue

                source_table_field = ""
                for key in ("update_fields", "create_fields"):
                    mapping = action.get(key)
                    if not isinstance(mapping, dict):
                        continue
                    for field_name, template in mapping.items():
                        if str(template or "").strip() == "{table_id}":
                            source_table_field = str(field_name or "").strip()
                            break
                    if source_table_field:
                        break

                dedupe_key = (target_app_token, target_table_id, source_record_field, source_table_field)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                targets.append(
                    {
                        "target_app_token": target_app_token,
                        "target_table_id": target_table_id,
                        "source_record_field": source_record_field,
                        "source_table_field": source_table_field,
                    }
                )

        return targets

    async def _reconcile_target_deletions(
        self,
        *,
        source_table_id: str,
        source_app_token: str,
        source_record_ids: set[str],
        scan_truncated: bool,
    ) -> dict[str, Any]:
        if not bool(self._settings.automation.sync_deletions_enabled):
            return {"status": "disabled", "deleted": 0, "failed": 0, "targets": []}

        if scan_truncated:
            return {
                "status": "skipped",
                "reason": "scan_truncated",
                "deleted": 0,
                "failed": 0,
                "targets": [],
            }

        targets = self._extract_sync_delete_targets(source_table_id, source_app_token)
        if not targets:
            return {"status": "skipped", "reason": "no_upsert_target", "deleted": 0, "failed": 0, "targets": []}

        page_size = max(1, min(self._settings.automation.scan_page_size, 500))
        max_pages = max(1, self._settings.automation.max_scan_pages)
        max_deletes = max(0, int(self._settings.automation.sync_deletions_max_per_run or 0))
        remaining = max_deletes if max_deletes > 0 else 10_000_000

        deleted = 0
        failed = 0
        target_summaries: list[dict[str, Any]] = []
        delete_limit_hit = False

        for target in targets:
            if remaining <= 0:
                delete_limit_hit = True
                break

            target_app_token = str(target.get("target_app_token") or "").strip()
            target_table_id = str(target.get("target_table_id") or "").strip()
            source_record_field = str(target.get("source_record_field") or "").strip()
            source_table_field = str(target.get("source_table_field") or "").strip()
            if not target_app_token or not target_table_id or not source_record_field:
                continue

            target_deleted = 0
            target_failed = 0
            page_token = ""
            pages = 0
            field_names = [source_record_field]
            if source_table_field:
                field_names.append(source_table_field)

            while pages < max_pages:
                page = await self._list_records_page(
                    target_app_token,
                    target_table_id,
                    page_token,
                    page_size,
                    field_names=field_names,
                )
                pages += 1
                items = page.get("items") or []
                for item in items:
                    if remaining <= 0:
                        delete_limit_hit = True
                        break
                    if not isinstance(item, dict):
                        continue

                    target_record_id, fields, _ = self._extract_record_meta(item)
                    if not target_record_id:
                        continue

                    source_record_value = self._to_scalar_text(fields.get(source_record_field))
                    if not source_record_value:
                        continue

                    if source_table_field:
                        source_table_value = self._to_scalar_text(fields.get(source_table_field))
                        if source_table_value and source_table_value != source_table_id:
                            continue

                    if source_record_value in source_record_ids:
                        continue

                    try:
                        await self._client.request(
                            "DELETE",
                            (
                                f"/bitable/v1/apps/{target_app_token}/tables/{target_table_id}"
                                f"/records/{target_record_id}"
                            ),
                        )
                        deleted += 1
                        target_deleted += 1
                        remaining -= 1
                    except FeishuAPIError as exc:
                        failed += 1
                        target_failed += 1
                        LOGGER.error("delete sync failed for target record %s: %s", target_record_id, exc)
                    except Exception as exc:
                        failed += 1
                        target_failed += 1
                        LOGGER.exception("delete sync failed for target record %s: %s", target_record_id, exc)

                if delete_limit_hit:
                    break
                if not page.get("has_more"):
                    break
                page_token = str(page.get("page_token") or "")
                if not page_token:
                    break

            target_summaries.append(
                {
                    "target_app_token": target_app_token,
                    "target_table_id": target_table_id,
                    "deleted": target_deleted,
                    "failed": target_failed,
                }
            )

        return {
            "status": "ok",
            "deleted": deleted,
            "failed": failed,
            "delete_limit_hit": delete_limit_hit,
            "targets": target_summaries,
        }

    async def scan_table(
        self,
        table_id: str | None = None,
        app_token: str | None = None,
        *,
        force_full: bool = False,
        reconcile_deletions: bool = False,
    ) -> dict[str, Any]:
        self._ensure_enabled()
        resolved_table_id, resolved_app_token = self._resolve_table_params(table_id, app_token)

        watch_plan = self._engine.get_watch_plan(resolved_table_id, app_token=resolved_app_token)
        watch_fields = None if _is_watch_mode_full(watch_plan) else _watch_fields(watch_plan)

        cursor = 0 if force_full else self._checkpoint.get(resolved_table_id)
        max_seen_cursor = cursor
        page_token = ""
        page_size = max(1, min(self._settings.automation.scan_page_size, 500))
        max_pages = max(1, self._settings.automation.max_scan_pages)

        counters = {
            "initialized": 0,
            "initialized_triggered": 0,
            "no_change": 0,
            "changed": 0,
            "duplicate_business": 0,
            "failed": 0,
            "scanned": 0,
            "deleted_synced": 0,
            "delete_failed": 0,
        }

        allow_new_record_trigger = bool(self._settings.automation.trigger_on_new_record_scan)
        require_checkpoint = bool(self._settings.automation.trigger_on_new_record_scan_requires_checkpoint)
        if not force_full and allow_new_record_trigger and require_checkpoint and cursor <= 0:
            allow_new_record_trigger = False

        max_new_record_triggers = max(0, int(self._settings.automation.new_record_scan_max_trigger_per_run or 0))
        new_record_triggered_count = 0
        source_record_ids: set[str] = set()
        scan_truncated = False

        pages = 0
        last_page_has_more = False
        while pages < max_pages:
            page = await self._list_records_page(
                resolved_app_token,
                resolved_table_id,
                page_token,
                page_size,
                field_names=watch_fields,
            )
            last_page_has_more = bool(page.get("has_more"))
            pages += 1
            items = page.get("items") or []

            for item in items:
                if not isinstance(item, dict):
                    continue
                counters["scanned"] += 1
                record_id, fields, modified_time = self._extract_record_meta(item)
                if not record_id:
                    continue
                if force_full:
                    source_record_ids.add(record_id)
                if modified_time and modified_time <= cursor:
                    continue
                if modified_time > max_seen_cursor:
                    max_seen_cursor = modified_time

                synthetic_event_id = (
                    f"scan:{resolved_table_id}:{record_id}:{modified_time or int(time.time() * 1000)}"
                )
                try:
                    filtered_fields = self._filter_fields_by_watch(fields, watch_plan)
                    if not filtered_fields:
                        filtered_fields = await self._fetch_record_fields_with_watch(
                            resolved_app_token,
                            resolved_table_id,
                            record_id,
                            field_names=watch_fields,
                        )
                        filtered_fields = self._filter_fields_by_watch(filtered_fields, watch_plan)

                    result = await self._process_record_changed(
                        synthetic_event_id,
                        resolved_app_token,
                        resolved_table_id,
                        record_id,
                        filtered_fields,
                        trigger_on_new_record=allow_new_record_trigger,
                    )
                    kind = str(result.get("kind") or "")
                    if kind in counters:
                        counters[kind] += 1

                    if kind == "initialized_triggered":
                        new_record_triggered_count += 1
                        if (
                            allow_new_record_trigger
                            and max_new_record_triggers > 0
                            and new_record_triggered_count >= max_new_record_triggers
                        ):
                            allow_new_record_trigger = False
                            LOGGER.warning(
                                "scan new-record trigger capped at %s for table %s",
                                max_new_record_triggers,
                                resolved_table_id,
                            )
                except FeishuAPIError as exc:
                    counters["failed"] += 1
                    LOGGER.error("scan_table fetch failed: %s", exc)
                except Exception as exc:
                    counters["failed"] += 1
                    LOGGER.exception("scan_table record processing failed: %s", exc)

            if not page.get("has_more"):
                break
            page_token = str(page.get("page_token") or "")
            if not page_token:
                break

        if pages >= max_pages and last_page_has_more:
            scan_truncated = True

        if max_seen_cursor > cursor:
            self._checkpoint.set(resolved_table_id, max_seen_cursor)

        deletion_result: dict[str, Any] | None = None
        if force_full and reconcile_deletions:
            deletion_result = await self._reconcile_target_deletions(
                source_table_id=resolved_table_id,
                source_app_token=resolved_app_token,
                source_record_ids=source_record_ids,
                scan_truncated=scan_truncated,
            )
            counters["deleted_synced"] = int(deletion_result.get("deleted") or 0)
            counters["delete_failed"] = int(deletion_result.get("failed") or 0)

        result: dict[str, Any] = {
            "status": "ok",
            "table_id": resolved_table_id,
            "from_cursor": cursor,
            "to_cursor": max_seen_cursor,
            "pages": pages,
            "counters": counters,
            "mode": "sync_scan" if force_full else "scan",
            "force_full": force_full,
            "scan_truncated": scan_truncated,
        }
        if deletion_result is not None:
            result["deletion_sync"] = deletion_result
        return result

    async def sync_table(self, table_id: str | None = None, app_token: str | None = None) -> dict[str, Any]:
        self._ensure_enabled()
        return await self.scan_table(
            table_id=table_id,
            app_token=app_token,
            force_full=True,
            reconcile_deletions=True,
        )

    async def scan_once_all_tables(self) -> dict[str, Any]:
        self._ensure_enabled()

        targets = self.get_poll_targets()
        results: list[dict[str, Any]] = []
        for target in targets:
            table_id = str(target.get("table_id") or "").strip()
            app_token = str(target.get("app_token") or "").strip()
            if not table_id or not app_token:
                continue

            target_key = (app_token, table_id)
            if target_key in self._poller_skip_targets:
                results.append(
                    {
                        "status": "skipped",
                        "table_id": table_id,
                        "app_token": app_token,
                        "reason": "wrong_table_id_cached",
                    }
                )
                continue

            try:
                result = await self.scan_table(table_id=table_id, app_token=app_token)
                result["app_token"] = app_token
                results.append(result)
            except FeishuAPIError as exc:
                if int(exc.code) == 1254004:
                    self._poller_skip_targets.add(target_key)
                    LOGGER.warning(
                        "poller disabled table due to WrongTableId: table_id=%s app_token=%s",
                        table_id,
                        app_token,
                    )
                LOGGER.exception("poller scan failed for table %s: %s", table_id, exc)
                results.append(
                    {
                        "status": "failed",
                        "table_id": table_id,
                        "app_token": app_token,
                        "error": self._format_error(exc),
                    }
                )
            except Exception as exc:
                LOGGER.exception("poller scan failed for table %s: %s", table_id, exc)
                results.append(
                    {
                        "status": "failed",
                        "table_id": table_id,
                        "app_token": app_token,
                        "error": self._format_error(exc),
                    }
                )

        return {
            "status": "ok",
            "mode": "poller_scan",
            "tables": [target.get("table_id") for target in targets],
            "results": results,
        }

    async def sync_once_all_tables(self) -> dict[str, Any]:
        self._ensure_enabled()

        targets = self.get_poll_targets()
        results: list[dict[str, Any]] = []
        for target in targets:
            table_id = str(target.get("table_id") or "").strip()
            app_token = str(target.get("app_token") or "").strip()
            if not table_id or not app_token:
                continue

            try:
                result = await self.sync_table(table_id=table_id, app_token=app_token)
                result["app_token"] = app_token
                results.append(result)
            except FeishuAPIError as exc:
                LOGGER.exception("sync scan failed for table %s: %s", table_id, exc)
                results.append(
                    {
                        "status": "failed",
                        "table_id": table_id,
                        "app_token": app_token,
                        "error": self._format_error(exc),
                    }
                )
            except Exception as exc:
                LOGGER.exception("sync scan failed for table %s: %s", table_id, exc)
                results.append(
                    {
                        "status": "failed",
                        "table_id": table_id,
                        "app_token": app_token,
                        "error": self._format_error(exc),
                    }
                )

        return {
            "status": "ok",
            "mode": "manual_sync",
            "tables": [target.get("table_id") for target in targets],
            "results": results,
        }
