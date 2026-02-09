from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.automation.actions import ActionExecutor
from src.automation.deadletter import DeadLetterStore
from src.automation.rules import RuleMatcher, RuleStore
from src.config import Settings


@dataclass
class RuleExecutionResult:
    rule_id: str
    name: str
    status: str
    error: str = ""
    matched: bool = False
    actions: list[dict[str, Any]] | None = None


class AutomationEngine:
    """规则引擎：加载规则、匹配规则并执行动作链。"""

    def __init__(self, settings: Settings, client: Any, rules_file: Path) -> None:
        self._settings = settings
        self._store = RuleStore(rules_file)
        self._matcher = RuleMatcher()
        self._executor = ActionExecutor(settings, client)

        dead_letter_path = Path(settings.automation.dead_letter_file)
        if not dead_letter_path.is_absolute():
            dead_letter_path = Path.cwd() / dead_letter_path
        self._dead_letters = DeadLetterStore(dead_letter_path)

    def list_poll_table_ids(self, default_table_id: str) -> list[str]:
        table_ids: list[str] = []
        if default_table_id:
            table_ids.append(default_table_id)
        for rule in self._store.load_all_enabled_rules():
            table = rule.get("table") or {}
            if not isinstance(table, dict):
                continue
            table_id = str(table.get("table_id") or "").strip()
            if table_id and table_id not in table_ids:
                table_ids.append(table_id)
        return table_ids

    def _build_default_status_action(self, status: str, error: str) -> dict[str, Any] | None:
        status_field = str(self._settings.automation.status_field or "").strip()
        error_field = str(self._settings.automation.error_field or "").strip()
        fields: dict[str, Any] = {}
        if status_field:
            fields[status_field] = status
        if error_field:
            fields[error_field] = error
        if not fields:
            return None
        return {
            "type": "bitable.update",
            "fields": fields,
        }

    @staticmethod
    def _as_action_list(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    async def _run_rule_pipeline(
        self,
        rule: dict[str, Any],
        context: dict[str, Any],
        app_token: str,
        table_id: str,
        record_id: str,
    ) -> RuleExecutionResult:
        pipeline = rule.get("pipeline") or {}
        if not isinstance(pipeline, dict):
            pipeline = {}

        before_actions = self._as_action_list(pipeline.get("before_actions"))
        actions = self._as_action_list(pipeline.get("actions"))
        success_actions = self._as_action_list(pipeline.get("success_actions"))
        error_actions = self._as_action_list(pipeline.get("error_actions"))

        if not before_actions:
            action = self._build_default_status_action("处理中", "")
            before_actions = [action] if action else []
        if not success_actions:
            action = self._build_default_status_action("成功", "")
            success_actions = [action] if action else []
        if not error_actions:
            action = self._build_default_status_action("失败", "{error}")
            error_actions = [action] if action else []

        all_action_results: list[dict[str, Any]] = []
        try:
            all_action_results.extend(
                await self._executor.run_actions(before_actions, context, app_token, table_id, record_id)
            )
            all_action_results.extend(
                await self._executor.run_actions(actions, context, app_token, table_id, record_id)
            )
            all_action_results.extend(
                await self._executor.run_actions(success_actions, context, app_token, table_id, record_id)
            )
            return RuleExecutionResult(
                rule_id=str(rule.get("rule_id") or ""),
                name=str(rule.get("name") or ""),
                status="success",
                matched=True,
                actions=all_action_results,
            )
        except Exception as exc:
            error_text = str(exc)
            error_context = dict(context)
            error_context["error"] = error_text
            try:
                all_action_results.extend(
                    await self._executor.run_actions(error_actions, error_context, app_token, table_id, record_id)
                )
            except Exception:
                pass
            self._dead_letters.write(
                {
                    "rule_id": str(rule.get("rule_id") or ""),
                    "rule_name": str(rule.get("name") or ""),
                    "event_id": str(context.get("event_id") or ""),
                    "app_token": str(context.get("app_token") or ""),
                    "table_id": str(context.get("table_id") or ""),
                    "record_id": str(context.get("record_id") or ""),
                    "error": error_text,
                }
            )
            return RuleExecutionResult(
                rule_id=str(rule.get("rule_id") or ""),
                name=str(rule.get("name") or ""),
                status="failed",
                error=error_text,
                matched=True,
                actions=all_action_results,
            )

    async def execute(
        self,
        app_token: str,
        table_id: str,
        record_id: str,
        event_id: str,
        old_fields: dict[str, Any],
        current_fields: dict[str, Any],
        diff: dict[str, Any],
    ) -> dict[str, Any]:
        rules = self._store.load_enabled_rules(table_id)
        results: list[dict[str, Any]] = []

        matched_count = 0
        success_count = 0
        failed_count = 0

        for rule in rules:
            matched = self._matcher.match(rule, old_fields, current_fields, diff)
            if not matched:
                continue

            matched_count += 1
            context = {
                "event_id": event_id,
                "table_id": table_id,
                "record_id": record_id,
                "app_token": app_token,
                "fields": current_fields,
                "old_fields": old_fields,
                "diff": diff,
                "error": "",
            }
            execution = await self._run_rule_pipeline(rule, context, app_token, table_id, record_id)

            if execution.status == "success":
                success_count += 1
            else:
                failed_count += 1

            results.append(
                {
                    "rule_id": execution.rule_id,
                    "name": execution.name,
                    "status": execution.status,
                    "error": execution.error,
                    "actions": execution.actions or [],
                }
            )

        status = "no_match"
        if matched_count > 0 and failed_count == 0:
            status = "success"
        if failed_count > 0:
            status = "failed"

        return {
            "status": status,
            "matched": matched_count,
            "succeeded": success_count,
            "failed": failed_count,
            "results": results,
        }
