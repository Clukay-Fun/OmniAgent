from __future__ import annotations

import asyncio
import logging
from typing import Any


LOGGER = logging.getLogger(__name__)


class SchemaPoller:
    """Schema 轮询器：按固定间隔刷新目标表字段结构。"""

    def __init__(self, service: Any, enabled: bool, interval_seconds: float) -> None:
        self._service = service
        self._enabled = bool(enabled)
        self._interval_seconds = max(0.1, float(interval_seconds))
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not self._enabled:
            LOGGER.info("schema poller disabled")
            return
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        LOGGER.info("schema poller started")

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop_event.set()
        await self._task
        self._task = None
        LOGGER.info("schema poller stopped")

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._service.refresh_schema_once_all_tables(triggered_by="poller")
            except Exception as exc:
                LOGGER.exception("schema poller refresh failed: %s", exc)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue
