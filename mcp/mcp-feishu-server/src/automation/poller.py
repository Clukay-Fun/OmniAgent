"""
描述: 自动化补偿轮询器。
主要功能:
    - 按固定间隔触发表扫描
    - 在事件缺失场景下进行补偿触发
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any


LOGGER = logging.getLogger(__name__)


class AutomationPoller:
    """轮询补偿器：按固定间隔扫描已配置表。"""

    def __init__(self, service: Any, enabled: bool, interval_seconds: float) -> None:
        self._service = service
        self._enabled = bool(enabled)
        self._interval_seconds = max(0.1, float(interval_seconds))
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not self._enabled:
            LOGGER.info("automation poller disabled")
            return
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        LOGGER.info("automation poller started")

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop_event.set()
        await self._task
        self._task = None
        LOGGER.info("automation poller stopped")

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._service.scan_once_all_tables()
            except Exception as exc:
                LOGGER.exception("automation poller scan failed: %s", exc)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue
