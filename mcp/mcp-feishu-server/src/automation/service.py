from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, cast

from src.automation.checkpoint import CheckpointStore
from src.automation.engine import AutomationEngine
from src.automation.runlog import RunLogStore
from src.automation.schema import SchemaStateStore, SchemaWatcher, WebhookNotifier
from src.automation.rules import build_business_hash_payload
from src.automation.snapshot import SnapshotStore
from src.automation.store import IdempotencyStore
from src.config import Settings
from src.feishu.client import FeishuAPIError


LOGGER = logging.getLogger(__name__)

EVENT_TYPE_RECORD_CHANGED = "drive.file.bitable_record_changed_v1"
EVENT_TYPE_FIELD_CHANGED = "drive.file.bitable_field_changed_v1"
SUPPORTED_EVENT_TYPES = {EVENT_TYPE_RECORD_CHANGED, EVENT_TYPE_FIELD_CHANGED}


class AutomationValidationError(ValueError):
    """自动化请求校验错误。"""


def _normalize_record_id(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_int_timestamp(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _is_watch_mode_full(plan: dict[str, Any]) -> bool:
    return str(plan.get("mode") or "full") == "full"


def _watch_fields(plan: dict[str, Any]) -> list[str]:
    fields = plan.get("fields")
    if not isinstance(fields, list):
        return []
    result: list[str] = []
    for item in fields:
        name = str(item or "").strip()
        if name:
            result.append(name)
    return result


class AutomationService:
    """自动化执行服务（Phase A/B: 事件、快照、规则、动作、幂等、扫描）。"""

    def __init__(self, settings: Settings, client: Any) -> None:
        self._settings = settings
        self._client = client

        storage_root = Path(settings.automation.storage_dir)
        if not storage_root.is_absolute():
            storage_root = Path.cwd() / storage_root
        storage_root.mkdir(parents=True, exist_ok=True)

        rules_file = Path(settings.automation.rules_file)
        if not rules_file.is_absolute():
            rules_file = Path.cwd() / rules_file

        runtime_state_file = Path(settings.automation.schema_runtime_state_file)
        if not runtime_state_file.is_absolute():
            runtime_state_file = Path.cwd() / runtime_state_file

        schema_cache_file = Path(settings.automation.schema_cache_file)
        if not schema_cache_file.is_absolute():
            schema_cache_file = Path.cwd() / schema_cache_file

        run_log_file = Path(settings.automation.run_log_file)
        if not run_log_file.is_absolute():
            run_log_file = Path.cwd() / run_log_file

        self._snapshot = SnapshotStore(storage_root / "snapshot.json")
        self._idempotency = IdempotencyStore(
            storage_root / "idempotency.json",
            event_ttl_seconds=settings.automation.event_ttl_seconds,
            business_ttl_seconds=settings.automation.business_ttl_seconds,
            max_keys=settings.automation.max_dedupe_keys,
        )
        self._checkpoint = CheckpointStore(storage_root / "checkpoint.json")
        self._engine = AutomationEngine(settings, client, rules_file, runtime_state_file=runtime_state_file)
        self._schema_watcher: SchemaWatcher | None = None
        if bool(settings.automation.schema_sync_enabled):
            self._schema_watcher = SchemaWatcher(
                client=client,
                rule_store=self._engine.rule_store,
                state_store=SchemaStateStore(
                    cache_file=schema_cache_file,
                    runtime_state_file=runtime_state_file,
                ),
                notifier=WebhookNotifier(
                    enabled=bool(settings.automation.schema_webhook_enabled),
                    url=str(settings.automation.schema_webhook_url or ""),
                    secret=str(settings.automation.schema_webhook_secret or ""),
                    timeout_seconds=float(settings.automation.schema_webhook_timeout_seconds),
                ),
                run_log_store=RunLogStore(run_log_file),
                policy={
                    "on_field_added": str(settings.automation.schema_policy_on_field_added or "auto_map_if_same_name"),
                    "on_field_removed": str(settings.automation.schema_policy_on_field_removed or "auto_remove"),
                    "on_field_renamed": str(settings.automation.schema_policy_on_field_renamed or "warn_only"),
                    "on_field_type_changed": str(
                        settings.automation.schema_policy_on_field_type_changed or "warn_only"
                    ),
                    "on_trigger_field_removed": str(
                        settings.automation.schema_policy_on_trigger_field_removed or "disable_rule"
                    ),
                },
            )
        self._poller_skip_targets: set[tuple[str, str]] = set()

    def _ensure_enabled(self) -> None:
        if not self._settings.automation.enabled:
            raise AutomationValidationError("automation is disabled, set automation.enabled=true")

    def _verify_token(self, token: str | None) -> None:
        expected = str(self._settings.automation.verification_token or "").strip()
        if not expected:
            return
        if token != expected:
            raise AutomationValidationError("invalid verification token")

    def _decrypt_envelope_if_needed(self, payload: dict[str, Any]) -> dict[str, Any]:
        encrypted = payload.get("encrypt")
        if not encrypted:
            return payload

        encrypt_key = str(self._settings.automation.encrypt_key or "").strip()
        if not encrypt_key:
            raise AutomationValidationError("encrypted payload received but encrypt_key is empty")

        try:
            from Crypto.Cipher import AES  # type: ignore
            from Crypto.Util.Padding import unpad  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise AutomationValidationError(
                "encrypted payload requires dependency 'pycryptodome'"
            ) from exc

        try:
            encrypted_bytes = base64.b64decode(str(encrypted))
            iv = encrypted_bytes[:16]
            cipher_text = encrypted_bytes[16:]
            key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
            cipher = AES.new(key, AES.MODE_CBC, iv)
            plain = unpad(cipher.decrypt(cipher_text), AES.block_size)
            decoded = json.loads(plain.decode("utf-8"))
        except Exception as exc:
            raise AutomationValidationError(f"failed to decrypt payload: {exc}") from exc

        if not isinstance(decoded, dict):
            raise AutomationValidationError("decrypted payload is not an object")
        return decoded

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
        )

    async def _process_record_changed(
        self,
        event_id: str,
        app_token: str,
        table_id: str,
        record_id: str,
        current_fields: dict[str, Any],
        trigger_on_new_record: bool,
    ) -> dict[str, Any]:
        old_fields = self._snapshot.load(table_id, record_id)

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

    async def handle_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_enabled()

        decoded = self._decrypt_envelope_if_needed(payload)

        request_type = decoded.get("type")
        if request_type == "url_verification":
            self._verify_token(decoded.get("token"))
            challenge = str(decoded.get("challenge") or "")
            if not challenge:
                raise AutomationValidationError("url_verification missing challenge")
            return {"kind": "challenge", "challenge": challenge}

        header = decoded.get("header") or {}
        event = decoded.get("event") or {}
        if not isinstance(header, dict) or not isinstance(event, dict):
            raise AutomationValidationError("invalid event envelope")

        event_id = str(header.get("event_id") or "").strip()
        event_type = str(header.get("event_type") or "").strip()
        self._verify_token(header.get("token"))

        LOGGER.info(
            "automation event received: event_id=%s event_type=%s",
            event_id or "-",
            event_type or "-",
        )

        if not event_id:
            raise AutomationValidationError("missing header.event_id")
        if event_type not in SUPPORTED_EVENT_TYPES:
            result = {
                "kind": "ignored",
                "reason": "unsupported_event_type",
                "event_id": event_id,
                "event_type": event_type,
            }
            LOGGER.info(
                "automation event ignored: event_id=%s reason=%s",
                event_id,
                result.get("reason"),
            )
            return result
        if self._idempotency.is_event_duplicate(event_id):
            result = {
                "kind": "duplicate_event",
                "event_id": event_id,
            }
            LOGGER.info("automation event duplicate: event_id=%s", event_id)
            return result

        app_token = str(event.get("app_token") or event.get("appToken") or "").strip()
        table_id = str(event.get("table_id") or event.get("tableId") or "").strip()

        if event_type == EVENT_TYPE_FIELD_CHANGED:
            self._idempotency.mark_event(event_id)
            if not app_token or not table_id:
                result = {
                    "kind": "ignored",
                    "reason": "field_changed_missing_app_or_table",
                    "event_id": event_id,
                }
                LOGGER.info(
                    "automation event ignored: event_id=%s reason=%s",
                    event_id,
                    result.get("reason"),
                )
                return result

            if bool(self._settings.automation.schema_sync_event_driven):
                schema_result = await self.refresh_schema_table(
                    table_id=table_id,
                    app_token=app_token,
                    triggered_by="event",
                )
                result = {
                    "kind": "schema_refreshed",
                    "event_id": event_id,
                    "app_token": app_token,
                    "table_id": table_id,
                    "schema": schema_result,
                }
                LOGGER.info(
                    "automation schema event processed: event_id=%s table_id=%s changed=%s",
                    event_id,
                    table_id,
                    schema_result.get("changed"),
                )
                return result

            result = {
                "kind": "ignored",
                "reason": "schema_sync_event_driven_disabled",
                "event_id": event_id,
                "app_token": app_token,
                "table_id": table_id,
            }
            LOGGER.info(
                "automation event ignored: event_id=%s reason=%s",
                event_id,
                result.get("reason"),
            )
            return result

        record_id = str(event.get("record_id") or event.get("recordId") or "").strip()

        if not app_token or not table_id or not record_id:
            raise AutomationValidationError("record_changed event missing app_token/table_id/record_id")

        result = await self.handle_record_changed(
            event_id,
            app_token,
            table_id,
            record_id,
            trigger_on_new_record=bool(self._settings.automation.trigger_on_new_record_event),
        )
        self._idempotency.mark_event(event_id)
        LOGGER.info(
            "automation event processed: event_id=%s kind=%s table_id=%s record_id=%s",
            event_id,
            result.get("kind"),
            table_id,
            record_id,
        )
        return result

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
                        "error": str(exc),
                    }
                )
            except Exception as exc:
                LOGGER.exception("schema refresh failed for table %s: %s", table_id, exc)
                results.append(
                    {
                        "status": "failed",
                        "table_id": table_id,
                        "app_token": app_token,
                        "error": str(exc),
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

    async def scan_table(self, table_id: str | None = None, app_token: str | None = None) -> dict[str, Any]:
        self._ensure_enabled()
        resolved_table_id, resolved_app_token = self._resolve_table_params(table_id, app_token)

        watch_plan = self._engine.get_watch_plan(resolved_table_id, app_token=resolved_app_token)
        watch_fields = None if _is_watch_mode_full(watch_plan) else _watch_fields(watch_plan)

        cursor = self._checkpoint.get(resolved_table_id)
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
        }

        allow_new_record_trigger = bool(self._settings.automation.trigger_on_new_record_scan)
        require_checkpoint = bool(self._settings.automation.trigger_on_new_record_scan_requires_checkpoint)
        if allow_new_record_trigger and require_checkpoint and cursor <= 0:
            allow_new_record_trigger = False

        max_new_record_triggers = max(0, int(self._settings.automation.new_record_scan_max_trigger_per_run or 0))
        new_record_triggered_count = 0

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
                counters["scanned"] += 1
                record_id, fields, modified_time = self._extract_record_meta(item)
                if not record_id:
                    continue
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

        if max_seen_cursor > cursor:
            self._checkpoint.set(resolved_table_id, max_seen_cursor)

        return {
            "status": "ok",
            "table_id": resolved_table_id,
            "from_cursor": cursor,
            "to_cursor": max_seen_cursor,
            "pages": pages,
            "counters": counters,
            "mode": "scan",
        }

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
                        "error": str(exc),
                    }
                )
            except Exception as exc:
                LOGGER.exception("poller scan failed for table %s: %s", table_id, exc)
                results.append(
                    {
                        "status": "failed",
                        "table_id": table_id,
                        "app_token": app_token,
                        "error": str(exc),
                    }
                )

        return {
            "status": "ok",
            "mode": "poller_scan",
            "tables": [target.get("table_id") for target in targets],
            "results": results,
        }
