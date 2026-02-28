"""
描述: 自动化服务执行侧 mixin。
主要功能:
    - 延迟与 cron 任务管理
    - 事件鉴权基础校验
"""

from __future__ import annotations

from datetime import datetime
import time
from typing import Any
import uuid

from croniter import croniter

from src.automation.cron_store import ACTIVE as CRON_ACTIVE
from src.automation.cron_store import VALID_CRON_STATUSES, CronJob
from src.automation.models import AutomationValidationError, VALID_DELAY_STATUSES


class AutomationExecutorMixin:
    @property
    def delay_store(self):
        return self._delay_store

    @property
    def cron_store(self):
        return self._cron_store

    async def execute_delayed_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._engine.execute_delayed_payload(payload)

    async def execute_cron_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._engine.execute_delayed_payload(payload)

    def list_delay_tasks(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        normalized_status = str(status or "").strip().lower()
        if normalized_status and normalized_status not in VALID_DELAY_STATUSES:
            allowed = ", ".join(sorted(VALID_DELAY_STATUSES))
            raise AutomationValidationError(f"invalid delay status: {normalized_status}. allowed: {allowed}")

        max_items = max(1, min(int(limit), 500))
        tasks = self._delay_store.list_tasks()
        tasks.sort(key=lambda item: (float(item.trigger_at), float(item.created_at), item.task_id))

        rows: list[dict[str, Any]] = []
        for task in tasks:
            if normalized_status and task.status != normalized_status:
                continue
            action_type = ""
            payload_action = task.payload.get("action") if isinstance(task.payload, dict) else None
            if isinstance(payload_action, dict):
                action_type = str(payload_action.get("type") or "").strip()
            rows.append(
                {
                    "task_id": task.task_id,
                    "rule_id": task.rule_id,
                    "status": task.status,
                    "trigger_at": float(task.trigger_at),
                    "created_at": float(task.created_at),
                    "executed_at": task.executed_at,
                    "error_detail": task.error_detail,
                    "action_type": action_type,
                }
            )
            if len(rows) >= max_items:
                break
        return rows

    def cancel_delay_task(self, task_id: str) -> dict[str, Any]:
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            raise AutomationValidationError("task_id is required")

        tasks = self._delay_store.list_tasks()
        matched = None
        for task in tasks:
            if task.task_id == normalized_task_id:
                matched = task
                break

        if matched is None:
            return {"status": "not_found", "task_id": normalized_task_id}

        cancelled = self._delay_store.cancel(normalized_task_id)
        if not cancelled:
            return {
                "status": "not_cancellable",
                "task_id": normalized_task_id,
                "current_status": matched.status,
            }

        return {"status": "cancelled", "task_id": normalized_task_id}

    @staticmethod
    def _next_cron_run_at(cron_expr: str, now_ts: float) -> float:
        base_dt = datetime.fromtimestamp(float(now_ts))
        return float(croniter(str(cron_expr), base_dt).get_next(float))

    def create_cron_job(
        self,
        *,
        cron_expr: str,
        action: dict[str, Any],
        context: dict[str, Any] | None = None,
        app_token: str = "",
        table_id: str = "",
        record_id: str = "",
        rule_id: str = "",
    ) -> dict[str, Any]:
        self._ensure_enabled()

        normalized_expr = str(cron_expr or "").strip()
        if not normalized_expr:
            raise AutomationValidationError("cron_expr is required")
        if not isinstance(action, dict) or not action:
            raise AutomationValidationError("action is required")

        now_value = time.time()
        try:
            next_run_at = self._next_cron_run_at(normalized_expr, now_value)
        except Exception as exc:
            raise AutomationValidationError(f"invalid cron expression: {exc}") from exc

        payload_context = context if isinstance(context, dict) else {}
        payload = {
            "action": action,
            "context": payload_context,
            "app_token": str(app_token or payload_context.get("app_token") or ""),
            "table_id": str(table_id or payload_context.get("table_id") or ""),
            "record_id": str(record_id or payload_context.get("record_id") or ""),
        }
        job = CronJob(
            job_id=str(uuid.uuid4()),
            cron_expr=normalized_expr,
            payload=payload,
            rule_id=str(rule_id or payload_context.get("rule_id") or "").strip(),
            status=CRON_ACTIVE,
            next_run_at=next_run_at,
            max_consecutive_failures=max(1, int(self._settings.automation.cron_max_consecutive_failures or 3)),
        )
        self._cron_store.schedule(job)
        return {
            "status": "scheduled",
            "job_id": job.job_id,
            "rule_id": job.rule_id,
            "cron_expr": job.cron_expr,
            "next_run_at": float(job.next_run_at),
            "max_consecutive_failures": int(job.max_consecutive_failures),
        }

    def list_cron_jobs(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        normalized_status = str(status or "").strip().lower()
        if normalized_status and normalized_status not in VALID_CRON_STATUSES:
            allowed = ", ".join(sorted(VALID_CRON_STATUSES))
            raise AutomationValidationError(f"invalid cron status: {normalized_status}. allowed: {allowed}")

        max_items = max(1, min(int(limit), 500))
        jobs = self._cron_store.list_jobs()
        jobs.sort(key=lambda item: (float(item.next_run_at), float(item.created_at), item.job_id))

        rows: list[dict[str, Any]] = []
        for job in jobs:
            if normalized_status and job.status != normalized_status:
                continue
            action_type = ""
            payload_action = job.payload.get("action") if isinstance(job.payload, dict) else None
            if isinstance(payload_action, dict):
                action_type = str(payload_action.get("type") or "").strip()

            rows.append(
                {
                    "job_id": job.job_id,
                    "rule_id": job.rule_id,
                    "status": job.status,
                    "cron_expr": job.cron_expr,
                    "next_run_at": float(job.next_run_at),
                    "created_at": float(job.created_at),
                    "updated_at": float(job.updated_at),
                    "last_run_at": job.last_run_at,
                    "last_success_at": job.last_success_at,
                    "last_failure_at": job.last_failure_at,
                    "last_error": job.last_error,
                    "pause_reason": job.pause_reason,
                    "paused_at": job.paused_at,
                    "cancelled_at": job.cancelled_at,
                    "consecutive_failures": int(job.consecutive_failures),
                    "max_consecutive_failures": int(job.max_consecutive_failures),
                    "execution_count": int(job.execution_count),
                    "action_type": action_type,
                }
            )
            if len(rows) >= max_items:
                break
        return rows

    def cancel_cron_job(self, job_id: str) -> dict[str, Any]:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            raise AutomationValidationError("job_id is required")

        matched = self._cron_store.get_job(normalized_job_id)
        if matched is None:
            return {"status": "not_found", "job_id": normalized_job_id}

        cancelled = self._cron_store.cancel(normalized_job_id)
        if not cancelled:
            return {
                "status": "not_cancellable",
                "job_id": normalized_job_id,
                "current_status": matched.status,
            }

        return {"status": "cancelled", "job_id": normalized_job_id}

    def resume_cron_job(self, job_id: str) -> dict[str, Any]:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            raise AutomationValidationError("job_id is required")

        matched = self._cron_store.get_job(normalized_job_id)
        if matched is None:
            return {"status": "not_found", "job_id": normalized_job_id}

        resumed = self._cron_store.resume(normalized_job_id)
        if not resumed:
            return {
                "status": "not_resumable",
                "job_id": normalized_job_id,
                "current_status": matched.status,
            }

        latest = self._cron_store.get_job(normalized_job_id)
        return {
            "status": "resumed",
            "job_id": normalized_job_id,
            "current_status": str(latest.status if latest is not None else CRON_ACTIVE),
            "next_run_at": float(latest.next_run_at if latest is not None else time.time()),
        }

    def _ensure_enabled(self) -> None:
        if not self._settings.automation.enabled:
            raise AutomationValidationError("automation is disabled, set automation.enabled=true")

    @staticmethod
    def _format_error(exc: Exception) -> str:
        message = str(exc).strip()
        if message:
            return message
        return exc.__class__.__name__

    def _verify_token(self, token: str | None) -> None:
        expected = str(self._settings.automation.verification_token or "").strip()
        if not expected:
            return
        if token != expected:
            raise AutomationValidationError("invalid verification token")
