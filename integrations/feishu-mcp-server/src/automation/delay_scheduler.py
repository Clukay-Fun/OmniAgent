"""
描述: delay 动作调度器。
主要功能:
    - 周期轮询到期任务
    - 执行并更新任务状态
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any


LOGGER = logging.getLogger(__name__)


class DelayScheduler:
    """延迟任务调度器。"""

    def __init__(
        self,
        service: Any,
        enabled: bool,
        interval_seconds: float = 5.0,
        cleanup_retention_seconds: float = 86400.0,
        worker_count: int = 1,
    ) -> None:
        self._service = service
        self._store = service.delay_store
        self._enabled = bool(enabled)
        self._interval_seconds = max(0.1, float(interval_seconds))
        self._cleanup_retention_seconds = max(1.0, float(cleanup_retention_seconds))
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
            LOGGER.info("delay scheduler disabled")
            return
        if self._worker_count > 1:
            LOGGER.warning(
                "delay scheduler multi-worker mode detected; keep only one scheduler active to avoid duplicate polling "
                "(workers=%s)",
                self._worker_count,
            )
        if self._task and not self._task.done():
            return
        self._get_stop_event().clear()
        self._task = asyncio.create_task(self._run_loop())
        LOGGER.info("delay scheduler started")

    async def stop(self) -> None:
        if not self._task:
            return
        self._get_stop_event().set()
        await self._task
        self._task = None
        LOGGER.info("delay scheduler stopped")

    async def _run_loop(self) -> None:
        stop_event = self._get_stop_event()
        while not stop_event.is_set():
            try:
                await self._poll_and_execute()
            except Exception as exc:
                LOGGER.exception("delay scheduler poll failed: %s", exc)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue

    async def _emit_completion_notification(
        self,
        *,
        task_id: str,
        status: str,
        payload: dict[str, Any],
        error_detail: str = "",
    ) -> None:
        notify = getattr(self._service, "notify_delay_execution_result", None)
        if not callable(notify):
            return
        try:
            maybe_awaitable = notify(
                task_id=str(task_id or ""),
                status=str(status),
                payload=payload if isinstance(payload, dict) else {},
                error_detail=str(error_detail or ""),
            )
            if asyncio.iscoroutine(maybe_awaitable):
                await maybe_awaitable
        except Exception as exc:
            LOGGER.warning("delay completion notification failed: %s", exc)

    async def _poll_and_execute(self, now_ts: float | None = None) -> None:
        now_value = float(now_ts if now_ts is not None else time.time())
        due_tasks = self._store.get_due_tasks(now_ts=now_value)
        for task in due_tasks:
            acquired = self._store.mark_executing(task.task_id)
            if not acquired:
                continue

            try:
                await self._service.execute_delayed_payload(task.payload)
                self._store.mark_completed(task.task_id)
                await self._emit_completion_notification(
                    task_id=task.task_id,
                    status="success",
                    payload=task.payload,
                )
            except Exception as exc:
                self._store.mark_failed(task.task_id, str(exc))
                await self._emit_completion_notification(
                    task_id=task.task_id,
                    status="failed",
                    payload=task.payload,
                    error_detail=str(exc),
                )

        self._store.cleanup_old(now_ts=now_value, retention_seconds=self._cleanup_retention_seconds)
