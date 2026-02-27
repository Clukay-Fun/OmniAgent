"""
描述: cron 周期任务存储。
主要功能:
    - 使用 JSONL 持久化 cron 任务队列
    - 提供状态迁移与并发安全的领取能力
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from threading import Lock
import time
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


ACTIVE = "active"
EXECUTING = "executing"
WAITING = "waiting"
PAUSED = "paused"
CANCELLED = "cancelled"

VALID_CRON_STATUSES = {ACTIVE, EXECUTING, WAITING, PAUSED, CANCELLED}


@dataclass
class CronJob:
    job_id: str
    cron_expr: str
    payload: dict[str, Any]
    rule_id: str = ""
    status: str = ACTIVE
    next_run_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_run_at: float | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None
    last_error: str | None = None
    pause_reason: str | None = None
    paused_at: float | None = None
    cancelled_at: float | None = None
    consecutive_failures: int = 0
    max_consecutive_failures: int = 3
    execution_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "cron_expr": self.cron_expr,
            "payload": dict(self.payload),
            "rule_id": self.rule_id,
            "status": self.status,
            "next_run_at": float(self.next_run_at),
            "created_at": float(self.created_at),
            "updated_at": float(self.updated_at),
            "last_run_at": self.last_run_at,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_error": self.last_error,
            "pause_reason": self.pause_reason,
            "paused_at": self.paused_at,
            "cancelled_at": self.cancelled_at,
            "consecutive_failures": int(self.consecutive_failures),
            "max_consecutive_failures": int(self.max_consecutive_failures),
            "execution_count": int(self.execution_count),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CronJob:
        raw_payload = payload.get("payload")
        normalized_payload = raw_payload if isinstance(raw_payload, dict) else {}

        def _opt_float(value: Any) -> float | None:
            if value is None:
                return None
            return float(value)

        return cls(
            job_id=str(payload.get("job_id") or "").strip(),
            cron_expr=str(payload.get("cron_expr") or "").strip(),
            payload=normalized_payload,
            rule_id=str(payload.get("rule_id") or "").strip(),
            status=str(payload.get("status") or ACTIVE).strip() or ACTIVE,
            next_run_at=float(payload.get("next_run_at") or 0.0),
            created_at=float(payload.get("created_at") or time.time()),
            updated_at=float(payload.get("updated_at") or time.time()),
            last_run_at=_opt_float(payload.get("last_run_at")),
            last_success_at=_opt_float(payload.get("last_success_at")),
            last_failure_at=_opt_float(payload.get("last_failure_at")),
            last_error=(str(payload.get("last_error")) if payload.get("last_error") is not None else None),
            pause_reason=(str(payload.get("pause_reason")) if payload.get("pause_reason") is not None else None),
            paused_at=_opt_float(payload.get("paused_at")),
            cancelled_at=_opt_float(payload.get("cancelled_at")),
            consecutive_failures=int(payload.get("consecutive_failures") or 0),
            max_consecutive_failures=max(1, int(payload.get("max_consecutive_failures") or 3)),
            execution_count=int(payload.get("execution_count") or 0),
        )


class CronStore:
    """Cron 任务存储（JSONL + 进程内锁 + 进程间锁 + 原子替换）。"""

    def __init__(self, file_path: str | Path = "automation_data/cron_queue.jsonl") -> None:
        self._file_path = Path(file_path)
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self._file_path.with_name(self._file_path.name + ".lock")
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    @contextmanager
    def _cross_process_lock(self):
        with self._lock_path.open("a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read_all(self) -> list[CronJob]:
        if not self._file_path.exists():
            return []
        raw = self._file_path.read_text(encoding="utf-8")
        if not raw.strip():
            return []

        jobs: list[CronJob] = []
        for line in raw.splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            job = CronJob.from_dict(parsed)
            if not job.job_id or not job.cron_expr:
                continue
            if job.status not in VALID_CRON_STATUSES:
                continue
            jobs.append(job)
        return jobs

    def _write_all(self, jobs: list[CronJob]) -> None:
        tmp_path = self._file_path.with_name(self._file_path.name + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as fp:
            for job in jobs:
                fp.write(json.dumps(job.to_dict(), ensure_ascii=False) + "\n")
        os.replace(tmp_path, self._file_path)

    @staticmethod
    def _find_job_index(jobs: list[CronJob], job_id: str) -> int:
        for idx, item in enumerate(jobs):
            if item.job_id == job_id:
                return idx
        return -1

    def list_jobs(self) -> list[CronJob]:
        with self._lock:
            with self._cross_process_lock():
                return self._read_all()

    def get_job(self, job_id: str) -> CronJob | None:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return None

        with self._lock:
            with self._cross_process_lock():
                jobs = self._read_all()
                idx = self._find_job_index(jobs, normalized_job_id)
                if idx < 0:
                    return None
                return jobs[idx]

    def schedule(self, job: CronJob) -> None:
        if not job.job_id:
            raise ValueError("job_id is required")
        if not job.cron_expr:
            raise ValueError("cron_expr is required")

        with self._lock:
            with self._cross_process_lock():
                jobs = self._read_all()
                if self._find_job_index(jobs, job.job_id) >= 0:
                    raise ValueError(f"duplicate cron job id: {job.job_id}")
                jobs.append(job)
                self._write_all(jobs)

    def activate_waiting(self, now_ts: float | None = None) -> int:
        now_value = float(now_ts if now_ts is not None else time.time())
        activated = 0
        with self._lock:
            with self._cross_process_lock():
                jobs = self._read_all()
                for idx, job in enumerate(jobs):
                    if job.status != WAITING:
                        continue
                    if float(job.next_run_at) > now_value:
                        continue
                    job.status = ACTIVE
                    job.updated_at = now_value
                    jobs[idx] = job
                    activated += 1

                if activated > 0:
                    self._write_all(jobs)
        return activated

    def acquire_due_jobs(self, now_ts: float | None = None, limit: int = 100) -> list[CronJob]:
        now_value = float(now_ts if now_ts is not None else time.time())
        max_items = max(1, int(limit))
        acquired: list[CronJob] = []

        with self._lock:
            with self._cross_process_lock():
                jobs = self._read_all()
                sorted_indexes = sorted(
                    range(len(jobs)),
                    key=lambda idx: (
                        float(jobs[idx].next_run_at),
                        float(jobs[idx].created_at),
                        jobs[idx].job_id,
                    ),
                )

                for idx in sorted_indexes:
                    if len(acquired) >= max_items:
                        break
                    job = jobs[idx]
                    if job.status != ACTIVE:
                        continue
                    if float(job.next_run_at) > now_value:
                        continue

                    job.status = EXECUTING
                    job.last_run_at = now_value
                    job.updated_at = now_value
                    jobs[idx] = job
                    acquired.append(job)

                if acquired:
                    self._write_all(jobs)

        return acquired

    def mark_success(self, job_id: str, *, next_run_at: float, executed_at: float | None = None) -> bool:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return False

        now_value = float(executed_at if executed_at is not None else time.time())
        with self._lock:
            with self._cross_process_lock():
                jobs = self._read_all()
                idx = self._find_job_index(jobs, normalized_job_id)
                if idx < 0:
                    return False

                job = jobs[idx]
                if job.status != EXECUTING:
                    return False

                job.status = WAITING
                job.next_run_at = float(next_run_at)
                job.last_success_at = now_value
                job.last_error = None
                job.pause_reason = None
                job.paused_at = None
                job.consecutive_failures = 0
                job.execution_count = int(job.execution_count) + 1
                job.updated_at = now_value
                jobs[idx] = job
                self._write_all(jobs)
                return True

    def mark_failure(
        self,
        job_id: str,
        *,
        next_run_at: float,
        detail: str,
        max_consecutive_failures: int,
        executed_at: float | None = None,
    ) -> dict[str, Any]:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return {"updated": False, "paused": False}

        now_value = float(executed_at if executed_at is not None else time.time())
        with self._lock:
            with self._cross_process_lock():
                jobs = self._read_all()
                idx = self._find_job_index(jobs, normalized_job_id)
                if idx < 0:
                    return {"updated": False, "paused": False}

                job = jobs[idx]
                if job.status != EXECUTING:
                    return {"updated": False, "paused": False, "current_status": job.status}

                threshold = max(1, int(job.max_consecutive_failures or max_consecutive_failures or 3))
                next_failures = int(job.consecutive_failures) + 1
                job.last_failure_at = now_value
                job.last_error = str(detail or "")
                job.execution_count = int(job.execution_count) + 1
                job.consecutive_failures = next_failures
                job.max_consecutive_failures = threshold

                paused = next_failures >= threshold
                if paused:
                    job.status = PAUSED
                    job.paused_at = now_value
                    job.pause_reason = f"consecutive failures reached {threshold}"
                else:
                    job.status = WAITING
                    job.next_run_at = float(next_run_at)

                job.updated_at = now_value
                jobs[idx] = job
                self._write_all(jobs)
                return {
                    "updated": True,
                    "paused": paused,
                    "consecutive_failures": next_failures,
                }

    def resume(self, job_id: str, now_ts: float | None = None) -> bool:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return False

        now_value = float(now_ts if now_ts is not None else time.time())
        with self._lock:
            with self._cross_process_lock():
                jobs = self._read_all()
                idx = self._find_job_index(jobs, normalized_job_id)
                if idx < 0:
                    return False

                job = jobs[idx]
                if job.status != PAUSED:
                    return False

                job.status = ACTIVE
                job.paused_at = None
                job.pause_reason = None
                job.consecutive_failures = 0
                if float(job.next_run_at) < now_value:
                    job.next_run_at = now_value
                job.updated_at = now_value
                jobs[idx] = job
                self._write_all(jobs)
                return True

    def cancel(self, job_id: str, now_ts: float | None = None) -> bool:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return False

        now_value = float(now_ts if now_ts is not None else time.time())
        with self._lock:
            with self._cross_process_lock():
                jobs = self._read_all()
                idx = self._find_job_index(jobs, normalized_job_id)
                if idx < 0:
                    return False

                job = jobs[idx]
                if job.status in {CANCELLED, EXECUTING}:
                    return False

                job.status = CANCELLED
                job.cancelled_at = now_value
                job.updated_at = now_value
                jobs[idx] = job
                self._write_all(jobs)
                return True
