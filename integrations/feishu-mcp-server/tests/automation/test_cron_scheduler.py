from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import sys
from typing import Any


MCP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_ROOT))

from src.automation.cron_scheduler import CronScheduler
from src.automation.cron_store import ACTIVE, PAUSED, WAITING, CronJob, CronStore


class _FakeService:
    def __init__(self, store: CronStore, should_fail: bool = False) -> None:
        self.cron_store = store
        self.should_fail = should_fail
        self.calls: list[dict[str, Any]] = []
        self.notify_calls: list[dict[str, Any]] = []

    async def execute_cron_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append(dict(payload))
        if self.should_fail:
            raise RuntimeError("cron run failed")
        return [{"status": "ok"}]

    async def notify_cron_execution_result(
        self,
        *,
        job_id: str,
        status: str,
        payload: dict[str, Any],
        error_detail: str = "",
    ) -> dict[str, Any]:
        self.notify_calls.append(
            {
                "job_id": job_id,
                "status": status,
                "payload": dict(payload),
                "error_detail": error_detail,
            }
        )
        return {"status": "sent"}


def test_poll_and_execute_marks_job_waiting_with_next_run(tmp_path: Path) -> None:
    store = CronStore(tmp_path / "cron_queue.jsonl")
    store.schedule(
        CronJob(
            job_id="job-1",
            cron_expr="*/5 * * * *",
            payload={"action": {"type": "log.write", "message": "ok"}},
            status=ACTIVE,
            next_run_at=60.0,
        )
    )
    service = _FakeService(store)
    scheduler = CronScheduler(service=service, enabled=True, interval_seconds=0.01)

    asyncio.run(scheduler._poll_and_execute(now_ts=61.0))

    assert len(service.calls) == 1
    current = store.get_job("job-1")
    assert current is not None
    assert current.status == WAITING
    assert current.next_run_at > 61.0


def test_poll_and_execute_pauses_after_consecutive_failures(tmp_path: Path) -> None:
    store = CronStore(tmp_path / "cron_queue.jsonl")
    store.schedule(
        CronJob(
            job_id="job-1",
            cron_expr="* * * * *",
            payload={"action": {"type": "log.write", "message": "ok"}},
            status=ACTIVE,
            next_run_at=60.0,
            max_consecutive_failures=2,
        )
    )
    service = _FakeService(store, should_fail=True)
    scheduler = CronScheduler(
        service=service,
        enabled=True,
        interval_seconds=0.01,
        max_consecutive_failures=2,
    )

    asyncio.run(scheduler._poll_and_execute(now_ts=60.0))
    first = store.get_job("job-1")
    assert first is not None
    assert first.status == WAITING
    assert first.consecutive_failures == 1

    asyncio.run(scheduler._poll_and_execute(now_ts=float(first.next_run_at)))
    second = store.get_job("job-1")
    assert second is not None
    assert second.status == PAUSED
    assert second.consecutive_failures == 2
    assert second.pause_reason and "consecutive failures" in second.pause_reason


def test_poll_and_execute_pauses_at_default_threshold_three(tmp_path: Path) -> None:
    store = CronStore(tmp_path / "cron_queue.jsonl")
    store.schedule(
        CronJob(
            job_id="job-default",
            cron_expr="* * * * *",
            payload={"action": {"type": "log.write", "message": "ok"}},
            status=ACTIVE,
            next_run_at=60.0,
        )
    )
    service = _FakeService(store, should_fail=True)
    scheduler = CronScheduler(service=service, enabled=True, interval_seconds=0.01)

    asyncio.run(scheduler._poll_and_execute(now_ts=60.0))
    first = store.get_job("job-default")
    assert first is not None
    assert first.status == WAITING
    assert first.consecutive_failures == 1
    assert not first.pause_reason

    asyncio.run(scheduler._poll_and_execute(now_ts=float(first.next_run_at)))
    second = store.get_job("job-default")
    assert second is not None
    assert second.status == WAITING
    assert second.consecutive_failures == 2
    assert not second.pause_reason

    asyncio.run(scheduler._poll_and_execute(now_ts=float(second.next_run_at)))
    third = store.get_job("job-default")
    assert third is not None
    assert third.status == PAUSED
    assert third.consecutive_failures == 3
    assert third.last_error == "cron run failed"


def test_start_logs_warning_when_worker_count_is_multi(tmp_path: Path, caplog: Any) -> None:
    store = CronStore(tmp_path / "cron_queue.jsonl")
    service = _FakeService(store)
    scheduler = CronScheduler(service=service, enabled=True, interval_seconds=0.01, worker_count=4)

    async def _run() -> None:
        await scheduler.start()
        await asyncio.sleep(0.01)
        await scheduler.stop()

    with caplog.at_level(logging.WARNING):
        asyncio.run(_run())

    assert "multi-worker mode detected" in caplog.text


def test_poll_and_execute_emits_completion_notification(tmp_path: Path) -> None:
    store = CronStore(tmp_path / "cron_queue.jsonl")
    store.schedule(
        CronJob(
            job_id="job-notify",
            cron_expr="*/5 * * * *",
            payload={
                "action": {"type": "log.write", "message": "ok"},
                "context": {"notify_target": {"chat_id": "oc_1"}},
            },
            status=ACTIVE,
            next_run_at=60.0,
        )
    )
    service = _FakeService(store)
    scheduler = CronScheduler(service=service, enabled=True, interval_seconds=0.01)

    asyncio.run(scheduler._poll_and_execute(now_ts=61.0))

    assert len(service.notify_calls) == 1
    assert service.notify_calls[0]["job_id"] == "job-notify"
    assert service.notify_calls[0]["status"] == "success"


def test_poll_and_execute_emits_failure_notification(tmp_path: Path) -> None:
    store = CronStore(tmp_path / "cron_queue.jsonl")
    store.schedule(
        CronJob(
            job_id="job-fail-notify",
            cron_expr="*/5 * * * *",
            payload={
                "action": {"type": "log.write", "message": "ok"},
                "context": {"notify_target": {"chat_id": "oc_1"}},
            },
            status=ACTIVE,
            next_run_at=60.0,
        )
    )
    service = _FakeService(store, should_fail=True)
    scheduler = CronScheduler(service=service, enabled=True, interval_seconds=0.01)

    asyncio.run(scheduler._poll_and_execute(now_ts=61.0))

    assert len(service.notify_calls) == 1
    assert service.notify_calls[0]["job_id"] == "job-fail-notify"
    assert service.notify_calls[0]["status"] == "failed"
    assert "cron run failed" in str(service.notify_calls[0]["error_detail"])
