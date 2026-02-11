from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

from src.automation.actions import ActionExecutionError, ActionExecutor
from src.automation.deadletter import DeadLetterStore
from src.automation.rules import RuleMatcher, RuleStore
from src.automation.runlog import RunLogStore
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

    def __init__(
        self,
        settings: Settings,
        client: Any,
        rules_file: Path,
        runtime_state_file: Path | None = None,
    ) -> None:
        self._settings = settings
        self._store = RuleStore(rules_file, runtime_state_file=runtime_state_file)
        self._matcher = RuleMatcher()
        self._executor = ActionExecutor(settings, client)

        dead_letter_path = Path(settings.automation.dead_letter_file)
        if not dead_letter_path.is_absolute():
            dead_letter_path = Path.cwd() / dead_letter_path
        self._dead_letters = DeadLetterStore(dead_letter_path)

        run_log_path = Path(settings.automation.run_log_file)
        if not run_log_path.is_absolute():
            run_log_path = Path.cwd() / run_log_path
        self._run_logs = RunLogStore(run_log_path)

    @property
    def rule_store(self) -> RuleStore:
        return self._store

    def list_poll_targets(self, default_table_id: str, default_app_token: str) -> list[dict[str, str]]:
        targets: list[dict[str, str]] = []
        if default_table_id:
            targets.append(
                {
                    "table_id": default_table_id,
                    "app_token": default_app_token,
                }
            )

        for rule in self._store.load_all_enabled_rules():
            table = rule.get("table") or {}
            if not isinstance(table, dict):
                continue

            table_id = str(table.get("table_id") or "").strip()
            if not table_id:
                continue

            app_token = str(table.get("app_token") or default_app_token or "").strip()

            exists = False
            for target in targets:
                if target.get("table_id") == table_id and target.get("app_token") == app_token:
                    exists = True
                    break
            if exists:
                continue

            targets.append(
                {
                    "table_id": table_id,
                    "app_token": app_token,
                }
            )

        return targets

    def get_watch_plan(self, table_id: str, app_token: str = "") -> dict[str, Any]:
        excluded = {
            name
            for name in (
                str(self._settings.automation.status_field or "").strip(),
                str(self._settings.automation.error_field or "").strip(),
            )
            if name
        }
        return self._store.get_watch_plan(table_id, excluded_fields=excluded, app_token=app_token)

    def _build_default_status_action(self, status: str, error: str) -> dict[str, Any] | None:
        if not bool(self._settings.automation.status_write_enabled):
            return None

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

    @staticmethod
    def _extract_trigger_change(rule: dict[str, Any], diff: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
        trigger = rule.get("trigger") or {}
        if not isinstance(trigger, dict):
            return None, None

        changed = diff.get("changed") or {}
        if not isinstance(changed, dict):
            changed = {}

        trigger_field = str(trigger.get("field") or "").strip() or None
        if trigger_field and trigger_field in changed:
            payload = changed.get(trigger_field)
            if isinstance(payload, dict):
                return trigger_field, {
                    "old": payload.get("old"),
                    "new": payload.get("new"),
                }

        if trigger_field:
            return trigger_field, None

        return None, {
            "fields": sorted(changed.keys()),
        }

    def _write_run_log(
        self,
        *,
        event_id: str,
        rule_id: str | None,
        record_id: str,
        table_id: str,
        trigger_field: str | None,
        changed: dict[str, Any] | None,
        actions: list[dict[str, Any]],
        result: str,
        error: str | None,
        retry_count: int,
        sent_to_dead_letter: bool,
        duration_ms: int,
    ) -> None:
        actions_executed: list[str] = []
        for action in actions:
            action_type = str(action.get("type") or "").strip()
            if action_type:
                actions_executed.append(action_type)

        self._run_logs.write(
            {
                "event_id": event_id,
                "rule_id": rule_id,
                "record_id": record_id,
                "table_id": table_id,
                "trigger_field": trigger_field,
                "changed": changed,
                "actions_executed": actions_executed,
                "result": result,
                "error": error,
                "retry_count": int(retry_count),
                "sent_to_dead_letter": bool(sent_to_dead_letter),
                "duration_ms": int(duration_ms),
            }
        )

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
        started_at = time.perf_counter()
        trigger_field, changed = self._extract_trigger_change(rule, context.get("diff") or {})
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
            retry_count = 0
            for action in all_action_results:
                retry_count = max(retry_count, int(action.get("retry_count") or 0))
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            self._write_run_log(
                event_id=str(context.get("event_id") or ""),
                rule_id=str(rule.get("rule_id") or "") or None,
                record_id=record_id,
                table_id=table_id,
                trigger_field=trigger_field,
                changed=changed,
                actions=all_action_results,
                result="success",
                error=None,
                retry_count=retry_count,
                sent_to_dead_letter=False,
                duration_ms=duration_ms,
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
            retry_count = 0
            if isinstance(exc, ActionExecutionError):
                retry_count = max(0, int(exc.attempts) - 1)
            try:
                all_action_results.extend(
                    await self._executor.run_actions(error_actions, error_context, app_token, table_id, record_id)
                )
                for action in all_action_results:
                    retry_count = max(retry_count, int(action.get("retry_count") or 0))
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
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            self._write_run_log(
                event_id=str(context.get("event_id") or ""),
                rule_id=str(rule.get("rule_id") or "") or None,
                record_id=record_id,
                table_id=table_id,
                trigger_field=trigger_field,
                changed=changed,
                actions=all_action_results,
                result="failed",
                error=error_text,
                retry_count=retry_count,
                sent_to_dead_letter=True,
                duration_ms=duration_ms,
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
        rules = self._store.load_enabled_rules(table_id, app_token=app_token)
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
                "fields": copy.deepcopy(current_fields),
                "old_fields": copy.deepcopy(old_fields),
                "diff": copy.deepcopy(diff),
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

        if matched_count == 0:
            changed = diff.get("changed") or {}
            fallback_changed = {"fields": sorted(changed.keys())} if isinstance(changed, dict) else None
            self._write_run_log(
                event_id=event_id,
                rule_id=None,
                record_id=record_id,
                table_id=table_id,
                trigger_field=None,
                changed=fallback_changed,
                actions=[],
                result="no_match",
                error=None,
                retry_count=0,
                sent_to_dead_letter=False,
                duration_ms=0,
            )

        return {
            "status": status,
            "matched": matched_count,
            "succeeded": success_count,
            "failed": failed_count,
            "results": results,
        }
