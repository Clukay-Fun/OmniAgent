from __future__ import annotations

from pathlib import Path
import sys


MCP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_ROOT))

from src.automation.cron_store import ACTIVE, CANCELLED, EXECUTING, PAUSED, WAITING, CronJob, CronStore


def _build_job(
    *,
    job_id: str = "job-1",
    status: str = ACTIVE,
    next_run_at: float = 1.0,
    max_consecutive_failures: int = 3,
) -> CronJob:
    return CronJob(
        job_id=job_id,
        cron_expr="*/5 * * * *",
        payload={"action": {"type": "log.write", "message": "ok"}},
        status=status,
        next_run_at=next_run_at,
        max_consecutive_failures=max_consecutive_failures,
    )


def test_schedule_and_list_jobs(tmp_path: Path) -> None:
    store = CronStore(tmp_path / "cron_queue.jsonl")
    store.schedule(_build_job())

    jobs = store.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].job_id == "job-1"
    assert jobs[0].status == ACTIVE


def test_acquire_due_jobs_marks_executing_atomically(tmp_path: Path) -> None:
    store = CronStore(tmp_path / "cron_queue.jsonl")
    store.schedule(_build_job())

    first = store.acquire_due_jobs(now_ts=2.0)
    second = store.acquire_due_jobs(now_ts=2.0)

    assert len(first) == 1
    assert first[0].status == EXECUTING
    assert second == []


def test_mark_success_moves_job_to_waiting_and_resets_failures(tmp_path: Path) -> None:
    store = CronStore(tmp_path / "cron_queue.jsonl")
    store.schedule(_build_job(max_consecutive_failures=4))

    acquired = store.acquire_due_jobs(now_ts=2.0)
    assert len(acquired) == 1

    updated = store.mark_success("job-1", next_run_at=300.0, executed_at=2.5)
    assert updated is True

    current = store.get_job("job-1")
    assert current is not None
    assert current.status == WAITING
    assert current.next_run_at == 300.0
    assert current.consecutive_failures == 0
    assert current.last_success_at == 2.5


def test_mark_failure_pauses_after_threshold(tmp_path: Path) -> None:
    store = CronStore(tmp_path / "cron_queue.jsonl")
    store.schedule(_build_job(max_consecutive_failures=2))

    first = store.acquire_due_jobs(now_ts=2.0)
    assert len(first) == 1
    first_fail = store.mark_failure(
        "job-1",
        next_run_at=60.0,
        detail="first error",
        max_consecutive_failures=2,
        executed_at=2.1,
    )
    assert first_fail["updated"] is True
    assert first_fail["paused"] is False

    activated = store.activate_waiting(now_ts=60.0)
    assert activated == 1

    second = store.acquire_due_jobs(now_ts=60.0)
    assert len(second) == 1
    second_fail = store.mark_failure(
        "job-1",
        next_run_at=120.0,
        detail="second error",
        max_consecutive_failures=2,
        executed_at=60.5,
    )
    assert second_fail["updated"] is True
    assert second_fail["paused"] is True

    current = store.get_job("job-1")
    assert current is not None
    assert current.status == PAUSED
    assert current.pause_reason and "consecutive failures" in current.pause_reason
    assert current.consecutive_failures == 2


def test_resume_and_cancel_status_flow(tmp_path: Path) -> None:
    store = CronStore(tmp_path / "cron_queue.jsonl")
    store.schedule(_build_job(status=PAUSED, next_run_at=1.0))

    resumed = store.resume("job-1", now_ts=10.0)
    assert resumed is True
    resumed_job = store.get_job("job-1")
    assert resumed_job is not None
    assert resumed_job.status == ACTIVE
    assert resumed_job.next_run_at == 10.0
    assert resumed_job.consecutive_failures == 0

    cancelled = store.cancel("job-1", now_ts=12.0)
    assert cancelled is True
    cancelled_job = store.get_job("job-1")
    assert cancelled_job is not None
    assert cancelled_job.status == CANCELLED


def test_cancel_rejects_executing_job(tmp_path: Path) -> None:
    store = CronStore(tmp_path / "cron_queue.jsonl")
    store.schedule(_build_job())
    store.acquire_due_jobs(now_ts=2.0)

    cancelled = store.cancel("job-1", now_ts=2.1)
    assert cancelled is False
