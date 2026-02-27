from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import sys
from typing import Any


MCP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_ROOT))

from src.automation.delay_scheduler import DelayScheduler
from src.automation.delay_store import DelayedTask, DelayStore
from src.config import Settings
from src.server import automation as automation_server


class _FakeService:
    def __init__(self, store: DelayStore, should_fail: bool = False) -> None:
        self.delay_store = store
        self.should_fail = should_fail
        self.calls: list[dict[str, Any]] = []

    async def execute_delayed_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append(dict(payload))
        if self.should_fail:
            raise RuntimeError("scheduler failed")
        return [{"status": "ok"}]


def test_poll_and_execute_marks_task_completed(tmp_path: Path) -> None:
    store = DelayStore(tmp_path / "delay_queue.jsonl")
    store.schedule(
        DelayedTask(
            task_id="task-1",
            rule_id="rule-1",
            trigger_at=1.0,
            payload={"action": {"type": "log.write", "message": "ok"}},
        )
    )
    service = _FakeService(store)
    scheduler = DelayScheduler(service=service, enabled=True, interval_seconds=0.01)

    asyncio.run(scheduler._poll_and_execute(now_ts=2.0))

    assert len(service.calls) == 1
    tasks = store.list_tasks()
    assert [item.status for item in tasks] == ["completed"]


def test_poll_and_execute_marks_task_failed_on_exception(tmp_path: Path) -> None:
    store = DelayStore(tmp_path / "delay_queue.jsonl")
    store.schedule(
        DelayedTask(
            task_id="task-1",
            rule_id="rule-1",
            trigger_at=1.0,
            payload={"action": {"type": "log.write", "message": "ok"}},
        )
    )
    service = _FakeService(store, should_fail=True)
    scheduler = DelayScheduler(service=service, enabled=True, interval_seconds=0.01)

    asyncio.run(scheduler._poll_and_execute(now_ts=2.0))

    assert len(service.calls) == 1
    tasks = store.list_tasks()
    assert [item.status for item in tasks] == ["failed"]
    assert tasks[0].error_detail == "scheduler failed"


def test_start_and_stop_scheduler_loop(tmp_path: Path) -> None:
    store = DelayStore(tmp_path / "delay_queue.jsonl")
    store.schedule(
        DelayedTask(
            task_id="task-1",
            rule_id="rule-1",
            trigger_at=1.0,
            payload={"action": {"type": "log.write", "message": "ok"}},
        )
    )
    service = _FakeService(store)
    scheduler = DelayScheduler(service=service, enabled=True, interval_seconds=0.01)

    async def _run() -> None:
        await scheduler.start()
        await asyncio.sleep(0.05)
        await scheduler.stop()

    asyncio.run(_run())

    assert len(service.calls) >= 1


def test_start_logs_warning_when_worker_count_is_multi(tmp_path: Path, caplog: Any) -> None:
    store = DelayStore(tmp_path / "delay_queue.jsonl")
    service = _FakeService(store)
    scheduler = DelayScheduler(service=service, enabled=True, interval_seconds=0.01, worker_count=3)

    async def _run() -> None:
        await scheduler.start()
        await asyncio.sleep(0.01)
        await scheduler.stop()

    with caplog.at_level(logging.WARNING):
        asyncio.run(_run())

    assert "multi-worker mode detected" in caplog.text


def test_resolve_scheduler_worker_count_prefers_settings(monkeypatch: Any) -> None:
    settings = Settings()
    settings.server.workers = 4
    monkeypatch.setenv("WEB_CONCURRENCY", "8")

    resolved = automation_server._resolve_scheduler_worker_count(settings)

    assert resolved == 4


def test_resolve_scheduler_worker_count_falls_back_to_env(monkeypatch: Any) -> None:
    settings = Settings()
    settings.server.workers = 1
    monkeypatch.setenv("WEB_CONCURRENCY", "6")

    resolved = automation_server._resolve_scheduler_worker_count(settings)

    assert resolved == 6
