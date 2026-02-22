from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import json
import logging
from pathlib import Path
import time
from typing import Any, Callable

import yaml

from src.utils.metrics import (
    record_automation_action,
    record_automation_dead_letter,
    record_automation_rule,
)


@dataclass
class RuleMatchResult:
    matched: bool
    reason: str = ""


@dataclass
class AutomationRule:
    rule_id: str
    source_table: str
    watched_fields: set[str]
    condition_mode: str
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    enabled: bool = True


@dataclass
class AutomationRuleSet:
    rules: list[AutomationRule]
    watched_fields_by_table: dict[str, set[str]]


class AutomationRuleLoader:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(__name__)

    def load(self, path: Path) -> AutomationRuleSet:
        if not path.exists():
            self._logger.info(
                "automation rules file not found",
                extra={
                    "event_code": "automation.rules.load_missing",
                    "rules_path": str(path),
                },
            )
            return AutomationRuleSet(rules=[], watched_fields_by_table={})

        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            self._logger.exception(
                "automation rules load failed",
                extra={
                    "event_code": "automation.rules.load_failed",
                    "rules_path": str(path),
                },
            )
            return AutomationRuleSet(rules=[], watched_fields_by_table={})

        watched_fields_by_table = self._normalize_watched_fields(payload.get("watched_fields"))
        raw_rules = payload.get("rules")
        if not isinstance(raw_rules, list):
            raw_rules = []

        normalized_rules: list[AutomationRule] = []
        for index, raw_rule in enumerate(raw_rules):
            rule = self._normalize_rule(raw_rule, watched_fields_by_table)
            if rule is None:
                self._logger.warning(
                    "automation rule skipped",
                    extra={
                        "event_code": "automation.rules.invalid_rule",
                        "rules_path": str(path),
                        "rule_index": index,
                    },
                )
                continue
            normalized_rules.append(rule)

        self._logger.info(
            "automation rules loaded",
            extra={
                "event_code": "automation.rules.loaded",
                "rules_path": str(path),
                "rule_count": len(normalized_rules),
            },
        )
        return AutomationRuleSet(rules=normalized_rules, watched_fields_by_table=watched_fields_by_table)

    def _normalize_watched_fields(self, payload: Any) -> dict[str, set[str]]:
        if not isinstance(payload, dict):
            return {}
        normalized: dict[str, set[str]] = {}
        for table_id, fields in payload.items():
            table_key = str(table_id or "").strip()
            if not table_key or not isinstance(fields, list):
                continue
            names = {str(name).strip() for name in fields if str(name).strip()}
            if names:
                normalized[table_key] = names
        return normalized

    def _normalize_rule(
        self,
        raw_rule: Any,
        watched_fields_by_table: dict[str, set[str]],
    ) -> AutomationRule | None:
        if not isinstance(raw_rule, dict):
            return None

        rule_id = str(raw_rule.get("rule_id") or "").strip()
        table_payload = raw_rule.get("table") if isinstance(raw_rule.get("table"), dict) else {}
        source_table = str(
            raw_rule.get("table_id")
            or raw_rule.get("source_table")
            or table_payload.get("table_id")
            or ""
        ).strip()
        if not rule_id or not source_table:
            return None

        enabled = bool(raw_rule.get("enabled", True))

        explicit_watched = raw_rule.get("watched_fields")
        watched_fields: set[str]
        if isinstance(explicit_watched, list):
            watched_fields = {str(name).strip() for name in explicit_watched if str(name).strip()}
        else:
            watched_fields = set(watched_fields_by_table.get(source_table, set()))

        condition_mode, conditions = self._normalize_conditions(raw_rule.get("trigger"))
        actions = self._normalize_actions(raw_rule)
        if not actions:
            return None

        return AutomationRule(
            rule_id=rule_id,
            source_table=source_table,
            watched_fields=watched_fields,
            condition_mode=condition_mode,
            conditions=conditions,
            actions=actions,
            enabled=enabled,
        )

    def _normalize_conditions(self, trigger: Any) -> tuple[str, list[dict[str, Any]]]:
        if not isinstance(trigger, dict):
            return "all", []

        raw_conditions: Any = trigger.get("all")
        condition_mode = "all"
        if not isinstance(raw_conditions, list):
            raw_conditions = trigger.get("any")
            if isinstance(raw_conditions, list):
                condition_mode = "any"
        if not isinstance(raw_conditions, list):
            return "all", []

        normalized: list[dict[str, Any]] = []
        for item in raw_conditions:
            parsed = self._normalize_single_condition(item)
            if parsed is not None:
                normalized.append(parsed)
        return condition_mode, normalized

    def _normalize_single_condition(self, item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None

        field_name = str(item.get("field") or "").strip()
        if not field_name:
            return None

        condition_payload = item.get("condition") if isinstance(item.get("condition"), dict) else item

        if "equals" in condition_payload:
            return {"field": field_name, "operator": "equals", "value": condition_payload.get("equals")}
        if "contains" in condition_payload:
            return {"field": field_name, "operator": "contains", "value": condition_payload.get("contains")}
        if "changed" in condition_payload:
            return {"field": field_name, "operator": "changed", "value": bool(condition_payload.get("changed"))}

        if not isinstance(item.get("condition"), dict) and "field" in item and len(item.keys()) == 2:
            return {"field": field_name, "operator": "equals", "value": item.get("condition")}
        return None

    def _normalize_actions(self, raw_rule: dict[str, Any]) -> list[dict[str, Any]]:
        direct_actions = raw_rule.get("actions")
        if isinstance(direct_actions, list):
            return [item for item in direct_actions if isinstance(item, dict)]

        pipeline = raw_rule.get("pipeline")
        if isinstance(pipeline, dict) and isinstance(pipeline.get("actions"), list):
            return [item for item in pipeline["actions"] if isinstance(item, dict)]
        return []


class AutomationRuleMatcher:
    def match(self, rule: AutomationRule, payload: dict[str, Any]) -> RuleMatchResult:
        if not rule.enabled:
            return RuleMatchResult(matched=False, reason="rule_disabled")
        table_id = str(payload.get("table_id") or "").strip()
        if table_id != rule.source_table:
            return RuleMatchResult(matched=False, reason="table_mismatch")

        changed_fields = self._extract_changed_fields(payload)
        if rule.watched_fields and not (rule.watched_fields & changed_fields):
            return RuleMatchResult(matched=False, reason="watched_fields_miss")

        if not rule.conditions:
            return RuleMatchResult(matched=True)

        condition_results = [self._match_condition(condition, payload) for condition in rule.conditions]
        if rule.condition_mode == "any":
            return RuleMatchResult(matched=any(condition_results), reason="condition_any")
        return RuleMatchResult(matched=all(condition_results), reason="condition_all")

    def _extract_changed_fields(self, payload: dict[str, Any]) -> set[str]:
        names: set[str] = set()
        raw_changed_fields = payload.get("changed_fields")
        if isinstance(raw_changed_fields, list):
            for name in raw_changed_fields:
                field_name = str(name).strip()
                if field_name:
                    names.add(field_name)

        diff_map = self._extract_diff_map(payload)
        names.update(diff_map.keys())
        return names

    def _extract_diff_map(self, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw_fragment = payload.get("raw_fragment")
        if not isinstance(raw_fragment, dict):
            return {}
        changed_fields = raw_fragment.get("changed_fields")
        if not isinstance(changed_fields, dict):
            return {}

        diff_map: dict[str, dict[str, Any]] = {}
        for field_name, diff in changed_fields.items():
            key = str(field_name).strip()
            if not key or not isinstance(diff, dict):
                continue
            diff_map[key] = {"old": diff.get("old"), "new": diff.get("new")}
        return diff_map

    def _match_condition(self, condition: dict[str, Any], payload: dict[str, Any]) -> bool:
        field_name = str(condition.get("field") or "").strip()
        operator = str(condition.get("operator") or "").strip()
        expected = condition.get("value")

        if not field_name or not operator:
            return False

        diff_map = self._extract_diff_map(payload)
        current_fields = payload.get("current_fields") if isinstance(payload.get("current_fields"), dict) else {}
        field_diff = diff_map.get(field_name, {})
        current_value = field_diff.get("new", current_fields.get(field_name))
        old_value = field_diff.get("old")

        if operator == "equals":
            return current_value == expected
        if operator == "contains":
            if isinstance(current_value, str):
                return str(expected) in current_value
            if isinstance(current_value, list):
                return expected in current_value
            return False
        if operator == "changed":
            changed = field_name in diff_map and old_value != current_value
            return changed if bool(expected) else not changed
        return False


class AutomationActionExecutor:
    def __init__(
        self,
        dead_letter_path: Path,
        dry_run: bool = True,
        status_write_enabled: bool = False,
        max_retries: int = 3,
        backoff_base_seconds: float = 0.2,
        sleeper: Callable[[float], None] | None = None,
        send_message_fn: Callable[..., Any] | None = None,
        bitable_update_fn: Callable[..., Any] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._dead_letter_path = dead_letter_path
        self._dry_run = dry_run
        self._status_write_enabled = status_write_enabled
        self._max_retries = max_retries
        self._backoff_base_seconds = backoff_base_seconds
        self._sleeper = sleeper or time.sleep
        self._send_message_fn = send_message_fn
        self._bitable_update_fn = bitable_update_fn
        self._logger = logger or logging.getLogger(__name__)

    def execute_rule(self, rule: AutomationRule, payload: dict[str, Any]) -> dict[str, Any]:
        action_results: list[dict[str, Any]] = []
        for action in rule.actions:
            action_results.append(self._execute_action_with_retry(rule, action, payload))
        return {
            "rule_id": rule.rule_id,
            "dry_run": self._dry_run,
            "actions": action_results,
        }

    def _execute_action_with_retry(
        self,
        rule: AutomationRule,
        action: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        action_type = str(action.get("type") or "unknown")
        for attempt in range(1, self._max_retries + 1):
            try:
                result = self._execute_action_once(action, payload)
                status = str(result.get("status") or "success")
                record_automation_action(action_type, status)
                self._logger.info(
                    "automation action executed",
                    extra={
                        "event_code": "automation.action.executed",
                        "rule_id": rule.rule_id,
                        "action_type": action_type,
                        "status": status,
                        "attempt": attempt,
                        "dry_run": self._dry_run,
                    },
                )
                return {
                    "action_type": action_type,
                    "status": status,
                    "attempts": attempt,
                    "dry_run": self._dry_run,
                }
            except Exception as exc:
                if attempt < self._max_retries:
                    delay = self._backoff_base_seconds * (2 ** (attempt - 1))
                    self._logger.warning(
                        "automation action retry",
                        extra={
                            "event_code": "automation.action.retry",
                            "rule_id": rule.rule_id,
                            "action_type": action_type,
                            "attempt": attempt,
                            "next_delay_seconds": delay,
                        },
                    )
                    self._sleeper(delay)
                    continue

                self._append_dead_letter(rule, action, payload, exc)
                record_automation_action(action_type, "failed")
                return {
                    "action_type": action_type,
                    "status": "failed",
                    "attempts": attempt,
                    "dry_run": self._dry_run,
                    "error": str(exc),
                }

        return {
            "action_type": action_type,
            "status": "failed",
            "attempts": self._max_retries,
            "dry_run": self._dry_run,
            "error": "unknown",
        }

    def _execute_action_once(self, action: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        action_type = str(action.get("type") or "unknown")

        if action_type == "log.write":
            message = str(action.get("message") or "")
            rendered = self._render_template(message, payload)
            self._logger.info(
                "automation dry-run log.write",
                extra={
                    "event_code": "automation.action.dry_run_log",
                    "action_type": action_type,
                    "message": rendered,
                    "dry_run": self._dry_run,
                },
            )
            return {"status": "success"}

        if action_type == "send_message":
            if self._dry_run:
                self._logger.info(
                    "automation dry-run send_message",
                    extra={
                        "event_code": "automation.action.dry_run_send_message",
                        "action_type": action_type,
                        "record_id": str(payload.get("record_id") or ""),
                        "dry_run": self._dry_run,
                    },
                )
                return {"status": "success"}
            sender = self._send_message_fn
            if not callable(sender):
                return {"status": "skipped_sender_unavailable"}
            rendered_text = self._render_template(
                str(action.get("message") or action.get("text") or "记录状态已更新"),
                payload,
            )
            receive_id = str(action.get("receive_id") or payload.get("chat_id") or "").strip()
            if not receive_id:
                return {"status": "skipped_missing_receive_id"}
            self._run_async(
                sender(
                    receive_id=receive_id,
                    msg_type="text",
                    content={"text": rendered_text},
                    receive_id_type=str(action.get("receive_id_type") or "chat_id"),
                    credential_source="org_b",
                )
            )
            return {"status": "success"}

        if action_type in {"bitable.update", "bitable.upsert"}:
            if self._dry_run:
                if not self._status_write_enabled:
                    return {"status": "skipped_status_write_disabled"}
                return {"status": "skipped_unsupported"}
            updater = self._bitable_update_fn
            if not callable(updater):
                return {"status": "skipped_updater_unavailable"}
            table_id = str(action.get("table_id") or payload.get("table_id") or "").strip()
            record_id = str(action.get("record_id") or payload.get("record_id") or "").strip()
            fields = action.get("fields") if isinstance(action.get("fields"), dict) else {}
            rendered_fields = {
                str(k): self._render_template(str(v), payload)
                for k, v in fields.items()
            }
            if not table_id or not record_id or not rendered_fields:
                return {"status": "skipped_missing_update_payload"}
            self._run_async(
                updater(
                    table_id=table_id,
                    record_id=record_id,
                    fields=rendered_fields,
                    credential_source="org_a",
                )
            )
            return {"status": "success"}

        return {"status": "skipped_unsupported"}

    def _render_template(self, template: str, payload: dict[str, Any]) -> str:
        rendered = template
        for key in ("record_id", "table_id", "event_id"):
            token = "{" + key + "}"
            rendered = rendered.replace(token, str(payload.get(key) or ""))
        return rendered

    def _run_async(self, awaitable: Any) -> None:
        if awaitable is None:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(awaitable)
            return
        except RuntimeError:
            pass
        asyncio.run(awaitable)

    def _append_dead_letter(
        self,
        rule: AutomationRule,
        action: dict[str, Any],
        payload: dict[str, Any],
        exc: Exception,
    ) -> None:
        record_automation_dead_letter()
        self._logger.error(
            "automation action moved to dead letter",
            extra={
                "event_code": "automation.action.dead_letter",
                "rule_id": rule.rule_id,
                "action_type": str(action.get("type") or "unknown"),
                "event_id": str(payload.get("event_id") or ""),
            },
        )
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "rule_id": rule.rule_id,
            "action_type": str(action.get("type") or "unknown"),
            "event_id": str(payload.get("event_id") or ""),
            "record_id": str(payload.get("record_id") or ""),
            "error": str(exc),
        }
        self._dead_letter_path.parent.mkdir(parents=True, exist_ok=True)
        with self._dead_letter_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def resolve_default_automation_rules_path(workspace_root: Path) -> Path:
    return workspace_root / "integrations" / "feishu-mcp-server" / "automation_rules.yaml"


def resolve_default_dead_letter_path(workspace_root: Path) -> Path:
    return workspace_root / "automation_data" / "dead_letters.jsonl"


def evaluate_rules(
    rules: list[AutomationRule],
    payload: dict[str, Any],
    matcher: AutomationRuleMatcher,
    logger: logging.Logger | None = None,
) -> list[AutomationRule]:
    matched: list[AutomationRule] = []
    event_id = str(payload.get("event_id") or "")
    for rule in rules:
        result = matcher.match(rule, payload)
        status = "matched" if result.matched else "skipped"
        record_automation_rule(rule.rule_id, status)
        if logger is not None:
            logger.info(
                "automation rule evaluated",
                extra={
                    "event_code": "automation.rule.evaluated",
                    "rule_id": rule.rule_id,
                    "status": status,
                    "reason": result.reason,
                    "event_id": event_id,
                },
            )
        if result.matched:
            matched.append(rule)
    return matched
