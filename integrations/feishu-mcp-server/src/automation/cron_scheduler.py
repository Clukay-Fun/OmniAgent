"""
描述: cron 周期任务调度器。
主要功能:
    - 周期轮询到期 cron 任务
    - 执行动作并推进任务状态机
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import time
from typing import Any

from croniter import croniter

from src.automation.cron_store import CronJob


LOGGER = logging.getLogger(__name__)


class CronScheduler:
    """Cron 任务调度器。"""

    def __init__(
        self,
        service: Any,
        enabled: bool,
        interval_seconds: float = 30.0,
        max_consecutive_failures: int = 3,
        worker_count: int = 1,
    ) -> None:
        self._service = service
        self._store = service.cron_store
        self._enabled = bool(enabled)
        self._interval_seconds = max(0.1, float(interval_seconds))
        self._max_consecutive_failures = max(1, int(max_consecutive_failures or 3))
        self._worker_count = max(1, int(worker_count or 1))
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    def _get_stop_event(self) -> asyncio.Event:
        event = self._stop_event
        if event is None:
            event = asyncio.Event()
            self._stop_event = event
        return event

    async def start(self) -> None:
        if not self._enabled:
            LOGGER.info("cron scheduler disabled")
            return
        if self._worker_count > 1:
            LOGGER.warning(
                "cron scheduler multi-worker mode detected; keep only one scheduler active to avoid duplicate polling "
                "(workers=%s)",
                self._worker_count,
            )
        if self._task and not self._task.done():
            return
        self._get_stop_event().clear()
        self._task = asyncio.create_task(self._run_loop())
        LOGGER.info("cron scheduler started")

    async def stop(self) -> None:
        if not self._task:
            return
        self._get_stop_event().set()
        await self._task
        self._task = None
        LOGGER.info("cron scheduler stopped")

    @staticmethod
    def _next_run_at(cron_expr: str, now_ts: float) -> float:
        base_dt = datetime.fromtimestamp(float(now_ts))
        return float(croniter(str(cron_expr), base_dt).get_next(float))

    async def _run_loop(self) -> None:
        stop_event = self._get_stop_event()
        while not stop_event.is_set():
            try:
                await self._poll_and_execute()
            except Exception as exc:
                LOGGER.exception("cron scheduler poll failed: %s", exc)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue

    async def _emit_completion_notification(self, *, job: CronJob, status: str, error_detail: str = "") -> None:
        notify = getattr(self._service, "notify_cron_execution_result", None)
        if not callable(notify):
            return
        try:
            maybe_awaitable = notify(
                job_id=str(job.job_id),
                status=str(status),
                payload=job.payload if isinstance(job.payload, dict) else {},
                error_detail=str(error_detail or ""),
            )
            if asyncio.iscoroutine(maybe_awaitable):
                await maybe_awaitable
        except Exception as exc:
            LOGGER.warning("cron completion notification failed: %s", exc)

    async def _poll_and_execute(self, now_ts: float | None = None) -> None:
        now_value = float(now_ts if now_ts is not None else time.time())
        self._store.activate_waiting(now_ts=now_value)
        due_jobs = self._store.acquire_due_jobs(now_ts=now_value)

        for job in due_jobs:
            job_now = float(now_ts if now_ts is not None else time.time())
            try:
                next_run_at = self._next_run_at(job.cron_expr, job_now)
            except Exception as exc:
                self._store.mark_failure(
                    job.job_id,
                    next_run_at=job_now,
                    detail=f"invalid cron expression: {exc}",
                    max_consecutive_failures=1,
                    executed_at=job_now,
                )
                await self._emit_completion_notification(
                    job=job,
                    status="failed",
                    error_detail=f"invalid cron expression: {exc}",
                )
                continue

            try:
                await self._service.execute_cron_payload(job.payload)
                self._store.mark_success(job.job_id, next_run_at=next_run_at, executed_at=job_now)
                await self._emit_completion_notification(job=job, status="success")
            except Exception as exc:
                self._store.mark_failure(
                    job.job_id,
                    next_run_at=next_run_at,
                    detail=str(exc),
                    max_consecutive_failures=self._max_consecutive_failures,
                    executed_at=job_now,
                )
                await self._emit_completion_notification(
                    job=job,
                    status="failed",
                    error_detail=str(exc),
                )
