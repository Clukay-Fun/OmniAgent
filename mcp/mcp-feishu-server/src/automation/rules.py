from __future__ import annotations

import json
from pathlib import Path
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


class RuleStore:
    """规则加载器：从 YAML 加载并过滤启用规则。"""

    def __init__(self, rules_file: Path) -> None:
        self._rules_file = rules_file

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

    def load_enabled_rules(self, table_id: str) -> list[dict[str, Any]]:
        enabled_rules: list[dict[str, Any]] = []
        for rule in self.load_all_enabled_rules():
            rule_table = rule.get("table") or {}
            if isinstance(rule_table, dict):
                rule_table_id = str(rule_table.get("table_id") or "").strip()
                if rule_table_id and rule_table_id != table_id:
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
        rules = parsed.get("rules")
        if not isinstance(rules, list):
            return []

        enabled_rules: list[dict[str, Any]] = []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if not bool(rule.get("enabled")):
                continue
            enabled_rules.append(rule)
        return enabled_rules


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
        changed_fields = set(changed.keys())

        if self._match_any_field_changed(trigger, changed_fields):
            return True

        return self._match_field_condition(trigger, current_fields, old_fields, changed)


def build_business_hash_payload(table_id: str, record_id: str, changed: dict[str, Any]) -> str:
    payload = {
        "table_id": table_id,
        "record_id": record_id,
        "changed": changed,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
