"""
描述: Schema 监听与策略执行模块。
主要功能:
    - 刷新表字段元数据并计算变更差异
    - 应用运行态策略并触发风险告警 webhook
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import httpx

from src.automation.rules import RuleStore


LOGGER = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _table_key(app_token: str, table_id: str) -> str:
    return f"{app_token}::{table_id}"


def _extract_trigger_fields(trigger: dict[str, Any]) -> set[str]:
    fields: set[str] = set()

    def collect(node: dict[str, Any]) -> None:
        field_name = str(node.get("field") or "").strip()
        if field_name:
            fields.add(field_name)

    collect(trigger)
    for group_key in ("all", "any"):
        items = trigger.get(group_key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                collect(item)
    return fields


class SchemaStateStore:
    def __init__(self, cache_file: Path, runtime_state_file: Path) -> None:
        self._cache_file = cache_file
        self._runtime_state_file = runtime_state_file
        self._lock = Lock()
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._runtime_state_file.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_load(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return parsed

    @staticmethod
    def _safe_dump(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_cache(self) -> dict[str, Any]:
        with self._lock:
            data = self._safe_load(self._cache_file)
            tables = data.get("tables")
            if not isinstance(tables, dict):
                tables = {}
            return {"tables": tables}

    def save_cache(self, cache: dict[str, Any]) -> None:
        with self._lock:
            payload = {
                "tables": cache.get("tables") if isinstance(cache.get("tables"), dict) else {},
            }
            self._safe_dump(self._cache_file, payload)

    def load_runtime_state(self) -> dict[str, Any]:
        with self._lock:
            data = self._safe_load(self._runtime_state_file)
            disabled_rules = data.get("disabled_rules")
            if not isinstance(disabled_rules, dict):
                disabled_rules = {}
            schema_tables = data.get("schema_tables")
            if not isinstance(schema_tables, dict):
                schema_tables = {}
            return {
                "disabled_rules": disabled_rules,
                "schema_tables": schema_tables,
            }

    def save_runtime_state(self, state: dict[str, Any]) -> None:
        with self._lock:
            payload = {
                "disabled_rules": state.get("disabled_rules")
                if isinstance(state.get("disabled_rules"), dict)
                else {},
                "schema_tables": state.get("schema_tables")
                if isinstance(state.get("schema_tables"), dict)
                else {},
            }
            self._safe_dump(self._runtime_state_file, payload)


class WebhookNotifier:
    def __init__(self, enabled: bool, url: str, secret: str, timeout_seconds: float, max_retries: int = 2) -> None:
        self._enabled = bool(enabled)
        self._url = str(url or "").strip()
        self._secret = str(secret or "").strip()
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._max_retries = max(0, int(max_retries))

    def _build_sign_payload(self) -> dict[str, str]:
        if not self._secret:
            return {}
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{self._secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        sign = base64.b64encode(digest).decode("utf-8")
        return {
            "timestamp": timestamp,
            "sign": sign,
        }

    async def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._enabled or not self._url:
            return {"status": "disabled"}

        body: dict[str, Any] = {
            "msg_type": "text",
            "content": {
                "text": json.dumps(payload, ensure_ascii=False),
            },
        }
        body.update(self._build_sign_payload())

        last_error: dict[str, Any] | None = None
        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout_seconds, trust_env=False) as client:
                    response = await client.post(self._url, json=body)
                if response.status_code < 400:
                    return {
                        "status": "sent",
                        "status_code": response.status_code,
                        "body": response.text,
                    }

                last_error = {
                    "status": "failed",
                    "status_code": response.status_code,
                    "body": response.text,
                }
                should_retry = response.status_code >= 500 and attempt < self._max_retries
                if not should_retry:
                    return last_error
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as exc:
                last_error = {
                    "status": "failed",
                    "error": str(exc),
                }
                if attempt >= self._max_retries:
                    return last_error

            await asyncio.sleep(0.5 * (2**attempt))

        return last_error or {"status": "failed", "error": "unknown webhook error"}


class SchemaWatcher:
    def __init__(
        self,
        *,
        client: Any,
        rule_store: RuleStore,
        state_store: SchemaStateStore,
        notifier: WebhookNotifier,
        run_log_store: Any,
        policy: dict[str, str],
    ) -> None:
        self._client = client
        self._rule_store = rule_store
        self._state_store = state_store
        self._notifier = notifier
        self._run_logs = run_log_store
        self._policy = policy

    @staticmethod
    def _normalize_fields(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        fields: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            field_id = str(item.get("field_id") or item.get("fieldId") or "").strip()
            name = str(item.get("field_name") or item.get("fieldName") or "").strip()
            if not field_id or not name:
                continue
            fields[field_id] = {
                "name": name,
                "type": int(item.get("type") or 0),
            }
        return fields

    @staticmethod
    def _build_runtime_schema(fields_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
        field_names: list[str] = []
        field_types: dict[str, int] = {}
        for meta in fields_by_id.values():
            name = str(meta.get("name") or "").strip()
            if not name:
                continue
            field_names.append(name)
            field_types[name] = int(meta.get("type") or 0)
        return {
            "field_names": sorted(set(field_names)),
            "field_types": field_types,
            "updated_at": _utc_now_iso(),
        }

    @staticmethod
    def _detect_alias_conflicts(fields_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[str]] = {}
        for field_id, meta in fields_by_id.items():
            name = str(meta.get("name") or "").strip()
            if not name:
                continue
            grouped.setdefault(name, []).append(field_id)

        conflicts: list[dict[str, Any]] = []
        for name, ids in grouped.items():
            if len(ids) <= 1:
                continue
            conflicts.append(
                {
                    "name": name,
                    "field_ids": sorted(ids),
                }
            )
        conflicts.sort(key=lambda item: str(item.get("name") or ""))
        return conflicts

    @staticmethod
    def _diff_fields(
        old_fields_by_id: dict[str, dict[str, Any]],
        new_fields_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        old_ids = set(old_fields_by_id.keys())
        new_ids = set(new_fields_by_id.keys())

        added = sorted(
            [new_fields_by_id[field_id].get("name") for field_id in (new_ids - old_ids)],
            key=lambda x: str(x),
        )
        removed = sorted(
            [old_fields_by_id[field_id].get("name") for field_id in (old_ids - new_ids)],
            key=lambda x: str(x),
        )

        renamed: list[dict[str, str]] = []
        type_changed: list[dict[str, Any]] = []
        for field_id in sorted(old_ids & new_ids):
            old_meta = old_fields_by_id.get(field_id) or {}
            new_meta = new_fields_by_id.get(field_id) or {}

            old_name = str(old_meta.get("name") or "")
            new_name = str(new_meta.get("name") or "")
            old_type = int(old_meta.get("type") or 0)
            new_type = int(new_meta.get("type") or 0)

            if old_name and new_name and old_name != new_name:
                renamed.append({"from": old_name, "to": new_name, "field_id": field_id})
            if old_type != new_type:
                type_changed.append(
                    {
                        "field_id": field_id,
                        "field_name": new_name or old_name,
                        "from": old_type,
                        "to": new_type,
                    }
                )

        return {
            "added": [str(x) for x in added if str(x)],
            "removed": [str(x) for x in removed if str(x)],
            "renamed": renamed,
            "type_changed": type_changed,
        }

    def _rules_for_table(self, app_token: str, table_id: str) -> list[dict[str, Any]]:
        rules = self._rule_store.load_all_rules()
        matched: list[dict[str, Any]] = []
        for rule in rules:
            table = rule.get("table") or {}
            if not isinstance(table, dict):
                continue
            rule_table_id = str(table.get("table_id") or "").strip()
            if rule_table_id != table_id:
                continue
            rule_app = str(table.get("app_token") or "").strip()
            if rule_app and rule_app != app_token:
                continue
            matched.append(rule)
        return matched

    def _write_schema_log(
        self,
        *,
        result: str,
        app_token: str,
        table_id: str,
        changed: dict[str, Any],
        actions_executed: list[str],
        error: str | None = None,
        rule_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "event_id": f"schema:{table_id}:{int(time.time())}",
            "rule_id": rule_id,
            "record_id": "",
            "table_id": table_id,
            "trigger_field": None,
            "changed": changed,
            "actions_executed": actions_executed,
            "result": result,
            "error": error,
            "retry_count": 0,
            "sent_to_dead_letter": False,
            "duration_ms": 0,
            "app_token": app_token,
        }
        if extra:
            payload.update(extra)
        self._run_logs.write(payload)

    async def _notify_if_needed(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._notifier.send(payload)
        is_drill = bool(payload.get("drill"))
        self._write_schema_log(
            result="schema_webhook_sent" if result.get("status") == "sent" else "schema_webhook_failed",
            app_token=str(payload.get("app_token") or ""),
            table_id=str(payload.get("table_id") or ""),
            changed={
                "payload": payload,
            },
            actions_executed=["schema.webhook"],
            error=None if result.get("status") == "sent" else str(result),
            extra={
                "webhook_result": result,
                "drill": is_drill,
                "schema_triggered_by": str(payload.get("triggered_by") or ""),
            },
        )
        return result

    async def send_risk_drill(self, app_token: str, table_id: str, triggered_by: str) -> dict[str, Any]:
        webhook_payload = {
            "kind": "schema_change_alert",
            "table": {
                "app_token": app_token,
                "table_id": table_id,
            },
            "change_type": "risk",
            "drill": True,
            "added": [],
            "removed": [],
            "type_changed": [
                {
                    "field_id": "drill_field",
                    "field_name": "schema_webhook_drill",
                    "from": 1,
                    "to": 2,
                }
            ],
            "alias_conflicts": [],
            "affected_rules": [],
            "disabled_rules": [],
            "action": {
                "mode": "drill_only",
                "description": "manual risk drill, no schema mutation",
            },
            "timestamp": _utc_now_iso(),
            "app_token": app_token,
            "table_id": table_id,
            "triggered_by": triggered_by,
        }
        result = await self._notify_if_needed(webhook_payload)
        self._write_schema_log(
            result="schema_webhook_drill",
            app_token=app_token,
            table_id=table_id,
            changed={
                "payload": webhook_payload,
            },
            actions_executed=["schema.webhook.drill"],
            error=None if result.get("status") == "sent" else str(result),
            extra={
                "webhook_result": result,
                "drill": True,
                "schema_triggered_by": triggered_by,
            },
        )
        return result

    def _disable_rule(self, runtime_state: dict[str, Any], rule_id: str, reason: str) -> bool:
        disabled = runtime_state.setdefault("disabled_rules", {})
        if not isinstance(disabled, dict):
            disabled = {}
            runtime_state["disabled_rules"] = disabled
        if rule_id in disabled:
            return False
        disabled[rule_id] = {
            "reason": reason,
            "at": _utc_now_iso(),
        }
        return True

    async def _list_all_fields(self, app_token: str, table_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {
                "page_size": 500,
            }
            if page_token:
                params["page_token"] = page_token
            response = await self._client.request(
                "GET",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
                params=params,
            )
            data = response.get("data") or {}
            page_items = data.get("items") or []
            if isinstance(page_items, list):
                items.extend([item for item in page_items if isinstance(item, dict)])
            if not bool(data.get("has_more")):
                break
            page_token = str(data.get("page_token") or "")
            if not page_token:
                break
        return items

    async def refresh_table(self, app_token: str, table_id: str, triggered_by: str) -> dict[str, Any]:
        items = await self._list_all_fields(app_token, table_id)

        key = _table_key(app_token, table_id)
        cache = self._state_store.load_cache()
        old_table = (cache.get("tables") or {}).get(key) if isinstance(cache.get("tables"), dict) else None
        old_fields_by_id = {}
        if isinstance(old_table, dict):
            old_fields = old_table.get("fields_by_id")
            if isinstance(old_fields, dict):
                old_fields_by_id = old_fields

        new_fields_by_id = self._normalize_fields(items)
        diff = self._diff_fields(old_fields_by_id, new_fields_by_id)
        alias_conflicts = self._detect_alias_conflicts(new_fields_by_id)

        tables = cache.setdefault("tables", {})
        if not isinstance(tables, dict):
            tables = {}
            cache["tables"] = tables
        tables[key] = {
            "app_token": app_token,
            "table_id": table_id,
            "fields_by_id": new_fields_by_id,
            "updated_at": _utc_now_iso(),
        }
        self._state_store.save_cache(cache)

        runtime_state = self._state_store.load_runtime_state()
        schema_tables = runtime_state.setdefault("schema_tables", {})
        if not isinstance(schema_tables, dict):
            schema_tables = {}
            runtime_state["schema_tables"] = schema_tables
        schema_tables[key] = {
            "app_token": app_token,
            "table_id": table_id,
            **self._build_runtime_schema(new_fields_by_id),
        }

        removed_fields = set([str(name) for name in diff.get("removed") or [] if str(name).strip()])
        table_rules = self._rules_for_table(app_token, table_id)
        policy_trigger_removed = str(self._policy.get("on_trigger_field_removed") or "disable_rule")
        disabled_rules_now: list[str] = []
        affected_rules: list[str] = []

        if removed_fields and policy_trigger_removed == "disable_rule":
            for rule in table_rules:
                rule_id = str(rule.get("rule_id") or "").strip()
                if not rule_id:
                    continue
                trigger = rule.get("trigger") or {}
                if not isinstance(trigger, dict):
                    continue
                trigger_fields = _extract_trigger_fields(trigger)
                hit = sorted(trigger_fields & removed_fields)
                if not hit:
                    continue
                affected_rules.append(rule_id)
                reason = f"trigger_field_removed:{','.join(hit)}"
                if self._disable_rule(runtime_state, rule_id, reason):
                    disabled_rules_now.append(rule_id)

        self._state_store.save_runtime_state(runtime_state)

        changed = bool(
            diff.get("added")
            or diff.get("removed")
            or diff.get("renamed")
            or diff.get("type_changed")
            or alias_conflicts
            or disabled_rules_now
        )
        if not changed:
            return {
                "status": "ok",
                "table_id": table_id,
                "app_token": app_token,
                "changed": False,
            }

        policy_applied = {
            "on_field_added": str(self._policy.get("on_field_added") or "auto_map_if_same_name"),
            "on_field_removed": str(self._policy.get("on_field_removed") or "auto_remove"),
            "on_field_renamed": str(self._policy.get("on_field_renamed") or "warn_only"),
            "on_field_type_changed": str(self._policy.get("on_field_type_changed") or "warn_only"),
            "on_trigger_field_removed": policy_trigger_removed,
        }

        self._write_schema_log(
            result="schema_changed",
            app_token=app_token,
            table_id=table_id,
            changed={
                "added": diff.get("added") or [],
                "removed": diff.get("removed") or [],
                "renamed": diff.get("renamed") or [],
                "type_changed": diff.get("type_changed") or [],
                "alias_conflicts": alias_conflicts,
                "disabled_rules": disabled_rules_now,
            },
            actions_executed=["schema.refresh"],
            extra={"schema_triggered_by": triggered_by},
        )

        self._write_schema_log(
            result="schema_policy_applied",
            app_token=app_token,
            table_id=table_id,
            changed={
                "policy": policy_applied,
                "added": diff.get("added") or [],
                "removed": diff.get("removed") or [],
                "renamed": diff.get("renamed") or [],
                "type_changed": diff.get("type_changed") or [],
                "alias_conflicts": alias_conflicts,
                "affected_rules": affected_rules,
            },
            actions_executed=["schema.policy"],
            extra={"schema_triggered_by": triggered_by},
        )

        for rule_id in disabled_rules_now:
            self._write_schema_log(
                result="schema_rule_disabled",
                app_token=app_token,
                table_id=table_id,
                changed={
                    "rule_id": rule_id,
                    "reason": "trigger_field_removed",
                },
                actions_executed=["schema.disable_rule"],
                rule_id=rule_id,
                extra={"schema_triggered_by": triggered_by},
            )

        risky = bool(diff.get("type_changed") or alias_conflicts or disabled_rules_now)
        if risky:
            webhook_payload = {
                "kind": "schema_change_alert",
                "table": {
                    "app_token": app_token,
                    "table_id": table_id,
                },
                "change_type": "risk",
                "added": diff.get("added") or [],
                "removed": diff.get("removed") or [],
                "type_changed": diff.get("type_changed") or [],
                "alias_conflicts": alias_conflicts,
                "affected_rules": sorted(set(affected_rules)),
                "disabled_rules": disabled_rules_now,
                "action": {
                    "trigger_field_removed": policy_trigger_removed,
                    "on_field_type_changed": policy_applied["on_field_type_changed"],
                    "on_field_renamed": policy_applied["on_field_renamed"],
                },
                "timestamp": _utc_now_iso(),
                "app_token": app_token,
                "table_id": table_id,
                "triggered_by": triggered_by,
            }
            await self._notify_if_needed(webhook_payload)

        return {
            "status": "ok",
            "table_id": table_id,
            "app_token": app_token,
            "changed": True,
            "diff": diff,
            "alias_conflicts": alias_conflicts,
            "disabled_rules": disabled_rules_now,
        }
