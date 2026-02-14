"""
描述: 自动化规则加载与匹配。
主要功能:
    - 读取/校验规则文件并构建匹配器
    - 支持字段条件、组合条件和模板渲染
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from string import Formatter
from typing import Any

import yaml


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _same_value(left: Any, right: Any) -> bool:
    return _normalize(left) == _normalize(right)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


_FORMATTER = Formatter()
_NON_FIELD_TEMPLATE_KEYS = {
    "event_id",
    "table_id",
    "record_id",
    "app_token",
    "error",
    "fields",
    "old_fields",
    "diff",
}


def _extract_template_fields(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    fields: set[str] = set()
    for _, field_name, _, _ in _FORMATTER.parse(value):
        if not field_name:
            continue
        key = str(field_name).strip()
        if not key or key in _NON_FIELD_TEMPLATE_KEYS:
            continue
        if any(ch in key for ch in (".", "[", "]")):
            continue
        fields.add(key)
    return fields


def _as_action_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


class RuleStore:
    """规则加载器：从 YAML 加载并过滤启用规则。"""

    def __init__(self, rules_file: Path, runtime_state_file: Path | None = None) -> None:
        self._rules_file = rules_file
        self._runtime_state_file = runtime_state_file

    def _load_runtime_state(self) -> dict[str, Any]:
        if self._runtime_state_file is None:
            return {}
        if not self._runtime_state_file.exists():
            return {}
        raw = self._runtime_state_file.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return parsed

    def _disabled_rule_ids(self) -> set[str]:
        runtime_state = self._load_runtime_state()
        disabled_rules = runtime_state.get("disabled_rules")
        if not isinstance(disabled_rules, dict):
            return set()
        return {
            str(rule_id).strip()
            for rule_id in disabled_rules.keys()
            if str(rule_id).strip()
        }

    def _table_available_fields(self, app_token: str, table_id: str) -> set[str] | None:
        runtime_state = self._load_runtime_state()
        schema_tables = runtime_state.get("schema_tables")
        if not isinstance(schema_tables, dict):
            return None
        key = f"{app_token}::{table_id}" if app_token else table_id
        schema = schema_tables.get(key)
        if not isinstance(schema, dict):
            return None
        names = schema.get("field_names")
        if not isinstance(names, list):
            return None
        return {
            str(name).strip()
            for name in names
            if str(name).strip()
        }

    def _load_raw(self) -> dict[str, Any]:
        if not self._rules_file.exists():
            return {}
        raw = self._rules_file.read_text(encoding="utf-8")
        if not raw.strip():
            return {}
        try:
            parsed = yaml.safe_load(raw) or {}
        except yaml.YAMLError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return parsed

    @staticmethod
    def _collect_defaults(parsed: dict[str, Any]) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        global_defaults = parsed.get("global")
        if isinstance(global_defaults, dict):
            defaults.update(global_defaults)
        explicit_defaults = parsed.get("defaults")
        if isinstance(explicit_defaults, dict):
            defaults.update(explicit_defaults)
        return defaults

    @staticmethod
    def _apply_defaults_to_rule(rule: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
        if not defaults:
            return dict(rule)

        resolved = copy.deepcopy(rule)

        default_source_app_token = str(
            defaults.get("source_app_token")
            or defaults.get("default_app_token")
            or defaults.get("app_token")
            or ""
        ).strip()
        default_source_table_id = str(
            defaults.get("source_table_id")
            or defaults.get("default_table_id")
            or defaults.get("table_id")
            or ""
        ).strip()

        rule_table = resolved.get("table")
        if not isinstance(rule_table, dict):
            rule_table = {}
        if default_source_app_token and not str(rule_table.get("app_token") or "").strip():
            rule_table["app_token"] = default_source_app_token
        if default_source_table_id and not str(rule_table.get("table_id") or "").strip():
            rule_table["table_id"] = default_source_table_id
        if rule_table:
            resolved["table"] = rule_table

        default_target_app_token = str(
            defaults.get("target_app_token") or default_source_app_token or ""
        ).strip()
        default_target_table_id = str(defaults.get("target_table_id") or "").strip()

        pipeline = resolved.get("pipeline")
        if isinstance(pipeline, dict):
            for key in ("before_actions", "actions", "success_actions", "error_actions"):
                actions = pipeline.get(key)
                if not isinstance(actions, list):
                    continue
                for action in actions:
                    if not isinstance(action, dict):
                        continue
                    action_type = str(action.get("type") or "").strip()
                    if action_type != "bitable.upsert":
                        continue
                    if default_target_app_token and not str(action.get("target_app_token") or "").strip():
                        action["target_app_token"] = default_target_app_token
                    if default_target_table_id and not str(action.get("target_table_id") or "").strip():
                        action["target_table_id"] = default_target_table_id

        return resolved

    def load_enabled_rules(self, table_id: str, app_token: str = "") -> list[dict[str, Any]]:
        disabled_ids = self._disabled_rule_ids()
        enabled_rules: list[dict[str, Any]] = []
        for rule in self.load_all_enabled_rules():
            rule_id = str(rule.get("rule_id") or "").strip()
            if rule_id and rule_id in disabled_ids:
                continue
            rule_table = rule.get("table") or {}
            if isinstance(rule_table, dict):
                rule_table_id = str(rule_table.get("table_id") or "").strip()
                if rule_table_id and rule_table_id != table_id:
                    continue
                rule_app_token = str(rule_table.get("app_token") or "").strip()
                if app_token and rule_app_token and rule_app_token != app_token:
                    continue
            enabled_rules.append(rule)

        enabled_rules.sort(
            key=lambda item: (
                int(item.get("priority") or 0),
                str(item.get("rule_id") or ""),
            ),
            reverse=True,
        )
        return enabled_rules

    def load_all_enabled_rules(self) -> list[dict[str, Any]]:
        parsed = self._load_raw()
        defaults = self._collect_defaults(parsed)
        rules = parsed.get("rules")
        if not isinstance(rules, list):
            return []

        enabled_rules: list[dict[str, Any]] = []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if not bool(rule.get("enabled")):
                continue
            enabled_rules.append(self._apply_defaults_to_rule(rule, defaults))
        return enabled_rules

    def load_all_rules(self) -> list[dict[str, Any]]:
        parsed = self._load_raw()
        defaults = self._collect_defaults(parsed)
        rules = parsed.get("rules")
        if not isinstance(rules, list):
            return []
        return [self._apply_defaults_to_rule(rule, defaults) for rule in rules if isinstance(rule, dict)]

    def _manual_watch_fields(self, table_id: str) -> set[str]:
        parsed = self._load_raw()
        watched = parsed.get("watched_fields")
        if not isinstance(watched, dict):
            return set()

        result: set[str] = set()
        for key in (table_id, "*", "default"):
            values = watched.get(key)
            if not isinstance(values, list):
                continue
            for item in values:
                field = str(item or "").strip()
                if field:
                    result.add(field)
        return result

    def _extract_rule_watch_fields(self, rule: dict[str, Any]) -> tuple[set[str], bool]:
        fields: set[str] = set()
        full_mode = False

        def collect_trigger(node: dict[str, Any]) -> None:
            nonlocal full_mode
            if bool(node.get("any_field_changed")):
                full_mode = True

            field_name = str(node.get("field") or "").strip()
            if field_name:
                fields.add(field_name)

        trigger = rule.get("trigger") or {}
        if isinstance(trigger, dict):
            collect_trigger(trigger)

            trigger_all = trigger.get("all")
            if isinstance(trigger_all, list):
                for clause in trigger_all:
                    if isinstance(clause, dict):
                        collect_trigger(clause)

            trigger_any = trigger.get("any")
            if isinstance(trigger_any, list):
                for clause in trigger_any:
                    if isinstance(clause, dict):
                        collect_trigger(clause)

        pipeline = rule.get("pipeline") or {}
        if not isinstance(pipeline, dict):
            return fields, full_mode

        all_actions: list[dict[str, Any]] = []
        all_actions.extend(_as_action_list(pipeline.get("before_actions")))
        all_actions.extend(_as_action_list(pipeline.get("actions")))
        all_actions.extend(_as_action_list(pipeline.get("success_actions")))
        all_actions.extend(_as_action_list(pipeline.get("error_actions")))

        for action in all_actions:
            action_type = str(action.get("type") or "").strip()

            if action_type == "calendar.create":
                for key in ("start_field", "end_field"):
                    name = str(action.get(key) or "").strip()
                    if name:
                        fields.add(name)

            for key in ("message", "summary", "summary_template", "description", "description_template"):
                fields.update(_extract_template_fields(action.get(key)))

            action_fields = action.get("fields")
            if isinstance(action_fields, dict):
                for value in action_fields.values():
                    fields.update(_extract_template_fields(value))

            for key in ("match_fields", "update_fields", "create_fields"):
                action_dict = action.get(key)
                if not isinstance(action_dict, dict):
                    continue
                for value in action_dict.values():
                    fields.update(_extract_template_fields(value))

        return fields, full_mode

    def get_watch_plan(
        self,
        table_id: str,
        excluded_fields: set[str] | None = None,
        app_token: str = "",
    ) -> dict[str, Any]:
        excluded = excluded_fields or set()
        fields = self._manual_watch_fields(table_id)
        rules = self.load_enabled_rules(table_id, app_token=app_token)

        full_mode = False
        for rule in rules:
            rule_fields, rule_full_mode = self._extract_rule_watch_fields(rule)
            fields.update(rule_fields)
            if rule_full_mode:
                full_mode = True

        if excluded:
            fields = {name for name in fields if name not in excluded}

        available_fields = self._table_available_fields(app_token=app_token, table_id=table_id)
        if available_fields is not None:
            fields = {name for name in fields if name in available_fields}

        if full_mode or not fields:
            return {
                "mode": "full",
                "fields": [],
            }

        return {
            "mode": "fields",
            "fields": sorted(fields),
        }


class RuleMatcher:
    """规则匹配器：支持 changed/equals/in/any_field_changed + exclude_fields。"""

    @staticmethod
    def _match_any_field_changed(trigger: dict[str, Any], changed_fields: set[str]) -> bool:
        if not bool(trigger.get("any_field_changed")):
            return False
        excluded = {
            str(name).strip()
            for name in _as_list(trigger.get("exclude_fields"))
            if str(name).strip()
        }
        effective_changes = changed_fields - excluded
        return bool(effective_changes)

    @staticmethod
    def _match_field_condition(
        trigger: dict[str, Any],
        current_fields: dict[str, Any],
        old_fields: dict[str, Any],
        changed: dict[str, dict[str, Any]],
    ) -> bool:
        field = str(trigger.get("field") or "").strip()
        if not field:
            return False

        condition = trigger.get("condition") or {}
        if not isinstance(condition, dict):
            condition = {}

        in_changed = field in changed
        if "changed" in condition:
            required_changed = bool(condition.get("changed"))
            if required_changed != in_changed:
                return False

        change_payload = changed.get(field) or {}
        old_value = change_payload.get("old", old_fields.get(field))
        new_value = change_payload.get("new", current_fields.get(field))

        if "equals" in condition and not _same_value(new_value, condition.get("equals")):
            return False

        if "in" in condition:
            candidates = _as_list(condition.get("in"))
            if isinstance(new_value, list):
                if not any(_same_value(item, candidate) for item in new_value for candidate in candidates):
                    return False
            elif not any(_same_value(new_value, candidate) for candidate in candidates):
                return False

        if bool(condition.get("old_not_equals_new")) and _same_value(old_value, new_value):
            return False

        return True

    def _match_single_trigger(
        self,
        trigger: dict[str, Any],
        old_fields: dict[str, Any],
        current_fields: dict[str, Any],
        changed: dict[str, dict[str, Any]],
    ) -> bool:
        changed_fields = set(changed.keys())
        if self._match_any_field_changed(trigger, changed_fields):
            return True
        return self._match_field_condition(trigger, current_fields, old_fields, changed)

    def match(
        self,
        rule: dict[str, Any],
        old_fields: dict[str, Any],
        current_fields: dict[str, Any],
        diff: dict[str, Any],
    ) -> bool:
        trigger = rule.get("trigger") or {}
        if not isinstance(trigger, dict):
            return False

        changed = diff.get("changed") or {}
        if not isinstance(changed, dict):
            changed = {}

        trigger_all = trigger.get("all")
        if isinstance(trigger_all, list) and trigger_all:
            for clause in trigger_all:
                if not isinstance(clause, dict):
                    return False
                if not self._match_single_trigger(clause, old_fields, current_fields, changed):
                    return False
            return True

        trigger_any = trigger.get("any")
        if isinstance(trigger_any, list) and trigger_any:
            for clause in trigger_any:
                if not isinstance(clause, dict):
                    continue
                if self._match_single_trigger(clause, old_fields, current_fields, changed):
                    return True
            return False

        return self._match_single_trigger(trigger, old_fields, current_fields, changed)


def build_business_hash_payload(table_id: str, record_id: str, changed: dict[str, Any]) -> str:
    payload = {
        "table_id": table_id,
        "record_id": record_id,
        "changed": changed,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
