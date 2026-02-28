"""
描述: 自动化服务编排层。
主要功能:
    - 负责依赖装配与外部入口门面
    - 通过 dispatcher/processor/executor 组合实现具体行为
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import time
from typing import Any

import httpx

from src.automation.checkpoint import CheckpointStore
from src.automation.cron_store import CronStore
from src.automation.delay_store import DelayStore
from src.automation.dispatcher import AutomationDispatcherMixin
from src.automation.engine import AutomationEngine
from src.automation.executor import AutomationExecutorMixin
from src.automation.models import AutomationValidationError
from src.automation.paths import resolve_config_base_dir, resolve_runtime_path
from src.automation.processor import AutomationProcessorMixin
from src.automation.runlog import RunLogStore
from src.automation.schema import SchemaStateStore, SchemaWatcher, WebhookNotifier
from src.automation.snapshot import SnapshotStore
from src.automation.store import IdempotencyStore
from src.config import Settings


LOGGER = logging.getLogger(__name__)


class AutomationService(AutomationDispatcherMixin, AutomationProcessorMixin, AutomationExecutorMixin):
    """自动化执行服务（组合 dispatcher/processor/executor 三个侧面）。"""

    def __init__(self, settings: Settings, client: Any) -> None:
        self._settings = settings
        self._client = client
        config_base_dir = resolve_config_base_dir()

        storage_root = resolve_runtime_path(settings.automation.storage_dir, base_dir=config_base_dir)
        storage_root.mkdir(parents=True, exist_ok=True)

        sqlite_raw = str(settings.automation.sqlite_db_file or "").strip()
        if sqlite_raw:
            sqlite_path = Path(sqlite_raw)
            if sqlite_path.is_absolute():
                sqlite_db_file = sqlite_path
            elif sqlite_path.parent == Path("."):
                sqlite_db_file = (storage_root / sqlite_path.name).resolve()
            else:
                sqlite_db_file = (storage_root.parent / sqlite_path).resolve()
        else:
            sqlite_db_file = storage_root / "automation.db"
        sqlite_db_file.parent.mkdir(parents=True, exist_ok=True)

        rules_file = resolve_runtime_path(settings.automation.rules_file, base_dir=config_base_dir)

        runtime_state_file = resolve_runtime_path(
            settings.automation.schema_runtime_state_file,
            base_dir=config_base_dir,
        )

        schema_cache_file = resolve_runtime_path(settings.automation.schema_cache_file, base_dir=config_base_dir)

        self._snapshot = SnapshotStore(storage_root / "snapshot.json", db_path=sqlite_db_file)
        self._idempotency = IdempotencyStore(
            storage_root / "idempotency.json",
            event_ttl_seconds=settings.automation.event_ttl_seconds,
            business_ttl_seconds=settings.automation.business_ttl_seconds,
            max_keys=settings.automation.max_dedupe_keys,
            db_path=sqlite_db_file,
        )
        self._checkpoint = CheckpointStore(storage_root / "checkpoint.json", db_path=sqlite_db_file)

        delay_queue_file = resolve_runtime_path(
            str(settings.automation.delay_queue_file or "").strip() or "delay_queue.jsonl",
            base_dir=config_base_dir,
            default_parent=storage_root,
        )
        self._delay_store = DelayStore(delay_queue_file, db_path=sqlite_db_file)

        cron_queue_file = resolve_runtime_path(
            str(settings.automation.cron_queue_file or "").strip() or "cron_queue.jsonl",
            base_dir=config_base_dir,
            default_parent=storage_root,
        )
        self._cron_store = CronStore(cron_queue_file, db_path=sqlite_db_file)

        self._engine = AutomationEngine(
            settings,
            client,
            rules_file,
            runtime_state_file=runtime_state_file,
            delay_store=self._delay_store,
            sqlite_db_path=sqlite_db_file,
        )
        self._schema_watcher: SchemaWatcher | None = None
        if bool(settings.automation.schema_sync_enabled):
            self._schema_watcher = SchemaWatcher(
                client=client,
                rule_store=self._engine.rule_store,
                state_store=SchemaStateStore(
                    cache_file=schema_cache_file,
                    runtime_state_file=runtime_state_file,
                    db_path=sqlite_db_file,
                ),
                notifier=WebhookNotifier(
                    enabled=bool(settings.automation.schema_webhook_enabled),
                    url=str(settings.automation.schema_webhook_url or ""),
                    secret=str(settings.automation.schema_webhook_secret or ""),
                    timeout_seconds=float(settings.automation.schema_webhook_timeout_seconds),
                ),
                run_log_store=RunLogStore(db_path=sqlite_db_file),
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

    def _find_enabled_rule(self, rule_id: str) -> dict[str, Any] | None:
        normalized_rule_id = str(rule_id or "").strip()
        if not normalized_rule_id:
            return None

        for rule in self._engine.rule_store.load_all_enabled_rules():
            current_rule_id = str(rule.get("rule_id") or "").strip()
            if current_rule_id == normalized_rule_id:
                return rule
        return None

    @staticmethod
    def _coerce_notify_target(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        chat_id = str(value.get("chat_id") or "").strip()
        user_id = str(value.get("user_id") or "").strip()
        target: dict[str, str] = {}
        if chat_id:
            target["chat_id"] = chat_id
        if user_id:
            target["user_id"] = user_id
        return target

    @classmethod
    def _resolve_notify_target_from_context(cls, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}

        resolved = cls._coerce_notify_target(value.get("notify_target"))

        chat_candidates = (
            value.get("notify_chat_id"),
            value.get("chat_id"),
            value.get("open_chat_id"),
        )
        if not resolved.get("chat_id"):
            for candidate in chat_candidates:
                chat_id = str(candidate or "").strip()
                if chat_id:
                    resolved["chat_id"] = chat_id
                    break

        user_candidates = (
            value.get("notify_user_id"),
            value.get("user_id"),
            value.get("open_id"),
        )
        if not resolved.get("user_id"):
            for candidate in user_candidates:
                user_id = str(candidate or "").strip()
                if user_id:
                    resolved["user_id"] = user_id
                    break

        triggered_by = value.get("triggered_by")
        if isinstance(triggered_by, dict):
            triggered_target = cls._resolve_notify_target_from_context(triggered_by)
            if triggered_target.get("chat_id") and not resolved.get("chat_id"):
                resolved["chat_id"] = triggered_target["chat_id"]
            if triggered_target.get("user_id") and not resolved.get("user_id"):
                resolved["user_id"] = triggered_target["user_id"]

        return resolved

    @classmethod
    def _extract_notify_target_from_event(cls, event: dict[str, Any]) -> dict[str, str]:
        resolved = cls._resolve_notify_target_from_context(event)

        operator_raw = event.get("operator")
        operator = operator_raw if isinstance(operator_raw, dict) else {}
        operator_id_raw = operator.get("operator_id")
        operator_id = operator_id_raw if isinstance(operator_id_raw, dict) else {}

        if not resolved.get("user_id"):
            for candidate in (
                operator_id.get("open_id"),
                operator.get("open_id"),
                operator.get("user_id"),
            ):
                user_id = str(candidate or "").strip()
                if user_id:
                    resolved["user_id"] = user_id
                    break

        return resolved

    @classmethod
    def _resolve_rule_notify_target(
        cls,
        *,
        rule: dict[str, Any] | None,
        inherited_target: dict[str, str] | None,
    ) -> dict[str, str]:
        resolved = cls._coerce_notify_target(inherited_target if isinstance(inherited_target, dict) else {})
        if not isinstance(rule, dict):
            return resolved

        rule_target = cls._resolve_notify_target_from_context(rule)
        if rule_target.get("chat_id"):
            resolved["chat_id"] = rule_target["chat_id"]
        if rule_target.get("user_id"):
            resolved["user_id"] = rule_target["user_id"]
        return resolved

    def _resolve_notify_target_from_payload(self, payload: dict[str, Any]) -> dict[str, str]:
        context_target = self._resolve_notify_target_from_context(payload.get("context"))
        payload_target = self._resolve_notify_target_from_context(payload)

        resolved = dict(context_target)
        if payload_target.get("chat_id"):
            resolved["chat_id"] = payload_target["chat_id"]
        if payload_target.get("user_id"):
            resolved["user_id"] = payload_target["user_id"]
        return resolved

    @staticmethod
    def _extract_action_type_from_payload(payload: dict[str, Any]) -> str:
        action_raw = payload.get("action")
        action = action_raw if isinstance(action_raw, dict) else {}
        return str(action.get("type") or "").strip()

    @staticmethod
    def _build_failure_guidance(job_id: str, detail: str = "") -> str:
        base = f"任务失败（JobID: {job_id}）。请根据 JobID 检查 run_logs/dead_letters 后重试。"
        cleaned_detail = str(detail or "").strip()
        if not cleaned_detail:
            return base
        return f"{base} 原因: {cleaned_detail}"

    async def _notify_automation_completed(
        self,
        *,
        job_type: str,
        job_id: str,
        status: str,
        summary: str,
        notify_target: dict[str, str] | None,
        error: str = "",
    ) -> dict[str, Any]:
        target = self._coerce_notify_target(notify_target if isinstance(notify_target, dict) else {})
        if not target:
            return {"status": "skipped", "reason": "missing_notify_target"}

        callback_url = str(self._settings.automation.notify_webhook_url or "").strip()
        callback_key = str(self._settings.automation.notify_api_key or "").strip()
        if not callback_url or not callback_key:
            return {"status": "skipped", "reason": "notify_webhook_not_configured"}

        normalized_job_type = str(job_type or "").strip().lower()
        if normalized_job_type not in {"rule", "cron", "delay"}:
            normalized_job_type = "rule"

        normalized_job_id = str(job_id or "").strip() or "unknown"
        normalized_status = "failed" if str(status or "").strip().lower() == "failed" else "success"
        summary_text = str(summary or "").strip() or "自动化任务执行完成。"
        error_text = str(error or "").strip()

        payload: dict[str, Any] = {
            "event": "automation.completed",
            "job_type": normalized_job_type,
            "job_id": normalized_job_id,
            "status": normalized_status,
            "notify_target": target,
            "summary": summary_text,
            "error": error_text if normalized_status == "failed" else "",
        }

        headers = {
            "x-automation-key": callback_key,
        }
        timeout_seconds = max(0.5, float(self._settings.automation.notify_timeout_seconds or 5.0))

        try:
            async with httpx.AsyncClient(timeout=timeout_seconds, trust_env=False) as client:
                response = await client.post(callback_url, json=payload, headers=headers)
            if response.status_code >= 400:
                LOGGER.warning(
                    "automation notify callback failed",
                    extra={
                        "event_code": "automation.notify.failed",
                        "job_type": normalized_job_type,
                        "job_id": normalized_job_id,
                        "status": normalized_status,
                        "status_code": int(response.status_code),
                    },
                )
                return {
                    "status": "failed",
                    "status_code": int(response.status_code),
                    "body": response.text,
                }
            return {
                "status": "sent",
                "status_code": int(response.status_code),
            }
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as exc:
            LOGGER.warning(
                "automation notify callback error: %s",
                exc,
                extra={
                    "event_code": "automation.notify.error",
                    "job_type": normalized_job_type,
                    "job_id": normalized_job_id,
                    "status": normalized_status,
                },
            )
            return {"status": "failed", "error": str(exc)}

    async def notify_cron_execution_result(
        self,
        *,
        job_id: str,
        status: str,
        payload: dict[str, Any],
        error_detail: str = "",
    ) -> dict[str, Any]:
        target = self._resolve_notify_target_from_payload(payload)
        action_type = self._extract_action_type_from_payload(payload)
        normalized_status = "failed" if str(status or "").strip().lower() == "failed" else "success"
        normalized_job_id = str(job_id or "").strip() or "unknown"
        summary = (
            f"Cron 任务执行成功（动作: {action_type or 'unknown'}）"
            if normalized_status == "success"
            else f"Cron 任务执行失败（动作: {action_type or 'unknown'}）"
        )
        error = self._build_failure_guidance(normalized_job_id, error_detail) if normalized_status == "failed" else ""
        return await self._notify_automation_completed(
            job_type="cron",
            job_id=normalized_job_id,
            status=normalized_status,
            summary=summary,
            notify_target=target,
            error=error,
        )

    async def notify_delay_execution_result(
        self,
        *,
        task_id: str,
        status: str,
        payload: dict[str, Any],
        error_detail: str = "",
    ) -> dict[str, Any]:
        target = self._resolve_notify_target_from_payload(payload)
        action_type = self._extract_action_type_from_payload(payload)
        normalized_status = "failed" if str(status or "").strip().lower() == "failed" else "success"
        normalized_task_id = str(task_id or "").strip() or "unknown"
        summary = (
            f"Delay 任务执行成功（动作: {action_type or 'unknown'}）"
            if normalized_status == "success"
            else f"Delay 任务执行失败（动作: {action_type or 'unknown'}）"
        )
        error = self._build_failure_guidance(normalized_task_id, error_detail) if normalized_status == "failed" else ""
        return await self._notify_automation_completed(
            job_type="delay",
            job_id=normalized_task_id,
            status=normalized_status,
            summary=summary,
            notify_target=target,
            error=error,
        )

    async def _notify_rule_execution_results(
        self,
        *,
        app_token: str,
        table_id: str,
        record_id: str,
        event_kind: str,
        rule_execution: dict[str, Any],
        inherited_notify_target: dict[str, str] | None,
    ) -> None:
        results_raw = rule_execution.get("results")
        results = results_raw if isinstance(results_raw, list) else []
        if not results:
            return

        enabled_rules = self._engine.rule_store.load_enabled_rules(table_id, app_token=app_token)
        rules_by_id: dict[str, dict[str, Any]] = {}
        for rule in enabled_rules:
            rule_id = str(rule.get("rule_id") or "").strip()
            if rule_id:
                rules_by_id[rule_id] = rule

        for result in results:
            if not isinstance(result, dict):
                continue
            rule_id = str(result.get("rule_id") or "").strip()
            if not rule_id:
                continue

            rule = rules_by_id.get(rule_id) or self._find_enabled_rule(rule_id)
            notify_target = self._resolve_rule_notify_target(rule=rule, inherited_target=inherited_notify_target)
            if not notify_target:
                continue

            status = "failed" if str(result.get("status") or "").strip().lower() == "failed" else "success"
            rule_name = str(result.get("name") or (rule.get("name") if isinstance(rule, dict) else "") or rule_id).strip()
            event_text = str(event_kind or "updated").strip() or "updated"
            summary = (
                f"规则 {rule_name} 执行成功（事件: {event_text}，记录: {record_id}）"
                if status == "success"
                else f"规则 {rule_name} 执行失败（事件: {event_text}，记录: {record_id}）"
            )
            detail = str(result.get("error") or "").strip()
            error = self._build_failure_guidance(rule_id, detail) if status == "failed" else ""
            await self._notify_automation_completed(
                job_type="rule",
                job_id=rule_id,
                status=status,
                summary=summary,
                notify_target=notify_target,
                error=error,
            )


__all__ = ["AutomationService", "AutomationValidationError"]
