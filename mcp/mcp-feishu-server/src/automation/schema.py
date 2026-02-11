from __future__ import annotations

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
            return {"disabled_rules": disabled_rules}

    def save_runtime_state(self, state: dict[str, Any]) -> None:
        with self._lock:
            payload = {
                "disabled_rules": state.get("disabled_rules") if isinstance(state.get("disabled_rules"), dict) else {},
            }
            self._safe_dump(self._runtime_state_file, payload)


class WebhookNotifier:
    def __init__(self, enabled: bool, url: str, secret: str, timeout_seconds: float) -> None:
        self._enabled = bool(enabled)
        self._url = str(url or "").strip()
        self._secret = str(secret or "").strip()
        self._timeout_seconds = max(1.0, float(timeout_seconds))

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

    async def send(self, title: str, lines: list[str]) -> dict[str, Any]:
        if not self._enabled or not self._url:
            return {"status": "disabled"}

        text = title
        if lines:
            text = title + "\n" + "\n".join(lines)

        payload: dict[str, Any] = {
            "msg_type": "text",
            "content": {
                "text": text,
            },
        }
        payload.update(self._build_sign_payload())

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds, trust_env=False) as client:
                response = await client.post(self._url, json=payload)
            return {
                "status": "sent" if response.status_code < 400 else "failed",
                "status_code": response.status_code,
                "body": response.text,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "error": str(exc),
            }


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
    def _field_name_set(fields_by_id: dict[str, dict[str, Any]]) -> set[str]:
        return {
            str(meta.get("name") or "").strip()
            for meta in fields_by_id.values()
            if str(meta.get("name") or "").strip()
        }

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

    async def _notify_if_needed(self, title: str, lines: list[str]) -> dict[str, Any]:
        result = await self._notifier.send(title=title, lines=lines)
        self._run_logs.write(
            {
                "event_id": "schema_webhook",
                "rule_id": None,
                "record_id": "",
                "table_id": "",
                "trigger_field": None,
                "changed": None,
                "actions_executed": ["schema.webhook"],
                "result": "schema_webhook_sent" if result.get("status") == "sent" else "schema_webhook_failed",
                "error": None if result.get("status") == "sent" else str(result),
                "retry_count": 0,
                "sent_to_dead_letter": False,
                "duration_ms": 0,
            }
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

    async def refresh_table(self, app_token: str, table_id: str, triggered_by: str) -> dict[str, Any]:
        response = await self._client.request(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
        )
        data = response.get("data") or {}
        items = data.get("items") or []
        if not isinstance(items, list):
            items = []

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

        changed = bool(diff.get("added") or diff.get("removed") or diff.get("renamed") or diff.get("type_changed"))
        if not changed:
            return {
                "status": "ok",
                "table_id": table_id,
                "app_token": app_token,
                "changed": False,
            }

        runtime_state = self._state_store.load_runtime_state()
        disabled_rules_now: list[str] = []

        removed_fields = set([str(name) for name in diff.get("removed") or [] if str(name).strip()])
        table_rules = self._rules_for_table(app_token, table_id)
        policy_trigger_removed = str(self._policy.get("on_trigger_field_removed") or "disable_rule")

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
                reason = f"trigger_field_removed:{','.join(hit)}"
                if self._disable_rule(runtime_state, rule_id, reason):
                    disabled_rules_now.append(rule_id)

        if disabled_rules_now:
            self._state_store.save_runtime_state(runtime_state)

        self._run_logs.write(
            {
                "event_id": f"schema:{table_id}:{int(time.time())}",
                "rule_id": None,
                "record_id": "",
                "table_id": table_id,
                "trigger_field": None,
                "changed": {
                    "added": diff.get("added") or [],
                    "removed": diff.get("removed") or [],
                    "renamed": diff.get("renamed") or [],
                    "type_changed": diff.get("type_changed") or [],
                    "disabled_rules": disabled_rules_now,
                },
                "actions_executed": ["schema.refresh"],
                "result": "schema_changed",
                "error": None,
                "retry_count": 0,
                "sent_to_dead_letter": False,
                "duration_ms": 0,
                "schema_triggered_by": triggered_by,
            }
        )

        risky = bool(diff.get("renamed") or diff.get("type_changed") or disabled_rules_now)
        if risky:
            lines: list[str] = [
                f"表: {table_id}",
                f"新增字段: {', '.join(diff.get('added') or []) or '-'}",
                f"删除字段: {', '.join(diff.get('removed') or []) or '-'}",
                f"改名字段: {len(diff.get('renamed') or [])}",
                f"类型变化: {len(diff.get('type_changed') or [])}",
            ]
            if disabled_rules_now:
                lines.append(f"规则已暂停: {', '.join(disabled_rules_now)}")
            await self._notify_if_needed("⚠️ Schema 变更提醒", lines)

        return {
            "status": "ok",
            "table_id": table_id,
            "app_token": app_token,
            "changed": True,
            "diff": diff,
            "disabled_rules": disabled_rules_now,
        }
