"""
描述: delay 动作任务存储。
主要功能:
    - 使用 JSONL 持久化延迟任务队列
    - 提供状态迁移与到期任务读取能力
"""

from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import contextmanager
try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None
import json
import os
from pathlib import Path
from threading import Lock
import time
from typing import Any


SCHEDULED = "scheduled"
EXECUTING = "executing"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL_STATUSES = {COMPLETED, FAILED, CANCELLED}


@dataclass
class DelayedTask:
    task_id: str
    rule_id: str
    trigger_at: float
    payload: dict[str, Any]
    status: str = SCHEDULED
    created_at: float = field(default_factory=time.time)
    executed_at: float | None = None
    error_detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "rule_id": self.rule_id,
            "trigger_at": float(self.trigger_at),
            "payload": dict(self.payload),
            "status": self.status,
            "created_at": float(self.created_at),
            "executed_at": self.executed_at,
            "error_detail": self.error_detail,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DelayedTask:
        raw_payload = payload.get("payload")
        normalized_payload = raw_payload if isinstance(raw_payload, dict) else {}
        executed_raw = payload.get("executed_at")
        executed_at = float(executed_raw) if executed_raw is not None else None
        error_raw = payload.get("error_detail")
        error_detail = str(error_raw) if error_raw is not None else None
        return cls(
            task_id=str(payload.get("task_id") or "").strip(),
            rule_id=str(payload.get("rule_id") or "").strip(),
            trigger_at=float(payload.get("trigger_at") or 0.0),
            payload=normalized_payload,
            status=str(payload.get("status") or SCHEDULED).strip() or SCHEDULED,
            created_at=float(payload.get("created_at") or time.time()),
            executed_at=executed_at,
            error_detail=error_detail,
        )


class DelayStore:
    """延迟任务存储（JSONL + 进程内锁 + 原子替换）。"""

    def __init__(self, file_path: str | Path = "automation_data/delay_queue.jsonl") -> None:
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

    def _read_all(self) -> list[DelayedTask]:
        if not self._file_path.exists():
            return []
        raw = self._file_path.read_text(encoding="utf-8")
        if not raw.strip():
            return []

        tasks: list[DelayedTask] = []
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
            task = DelayedTask.from_dict(parsed)
            if not task.task_id:
                continue
            tasks.append(task)
        return tasks

    def _write_all(self, tasks: list[DelayedTask]) -> None:
        tmp_path = self._file_path.with_name(self._file_path.name + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as fp:
            for task in tasks:
                fp.write(json.dumps(task.to_dict(), ensure_ascii=False) + "\n")
        os.replace(tmp_path, self._file_path)

    @staticmethod
    def _find_task_index(tasks: list[DelayedTask], task_id: str) -> int:
        for idx, item in enumerate(tasks):
            if item.task_id == task_id:
                return idx
        return -1

    def list_tasks(self) -> list[DelayedTask]:
        with self._lock:
            with self._cross_process_lock():
                return self._read_all()

    def schedule(self, task: DelayedTask) -> None:
        if not task.task_id:
            raise ValueError("task_id is required")
        with self._lock:
            with self._cross_process_lock():
                tasks = self._read_all()
                if self._find_task_index(tasks, task.task_id) >= 0:
                    raise ValueError(f"duplicate delay task id: {task.task_id}")
                tasks.append(task)
                self._write_all(tasks)

    def get_due_tasks(self, now_ts: float | None = None, limit: int = 100) -> list[DelayedTask]:
        now_value = float(now_ts if now_ts is not None else time.time())
        max_items = max(1, int(limit))
        with self._lock:
            with self._cross_process_lock():
                tasks = self._read_all()
        due = [
            item
            for item in tasks
            if item.status == SCHEDULED and float(item.trigger_at) <= now_value
        ]
        due.sort(key=lambda item: (float(item.trigger_at), float(item.created_at), item.task_id))
        return due[:max_items]

    def mark_executing(self, task_id: str) -> bool:
        if not task_id:
            return False
        with self._lock:
            with self._cross_process_lock():
                tasks = self._read_all()
                idx = self._find_task_index(tasks, task_id)
                if idx < 0:
                    return False
                task = tasks[idx]
                if task.status != SCHEDULED:
                    return False
                task.status = EXECUTING
                task.error_detail = None
                tasks[idx] = task
                self._write_all(tasks)
                return True

    def mark_completed(self, task_id: str) -> bool:
        if not task_id:
            return False
        with self._lock:
            with self._cross_process_lock():
                tasks = self._read_all()
                idx = self._find_task_index(tasks, task_id)
                if idx < 0:
                    return False
                task = tasks[idx]
                if task.status not in {SCHEDULED, EXECUTING}:
                    return False
                task.status = COMPLETED
                task.executed_at = time.time()
                task.error_detail = None
                tasks[idx] = task
                self._write_all(tasks)
                return True

    def mark_failed(self, task_id: str, detail: str) -> bool:
        if not task_id:
            return False
        with self._lock:
            with self._cross_process_lock():
                tasks = self._read_all()
                idx = self._find_task_index(tasks, task_id)
                if idx < 0:
                    return False
                task = tasks[idx]
                if task.status not in {SCHEDULED, EXECUTING}:
                    return False
                task.status = FAILED
                task.executed_at = time.time()
                task.error_detail = str(detail or "")
                tasks[idx] = task
                self._write_all(tasks)
                return True

    def cancel(self, task_id: str) -> bool:
        if not task_id:
            return False
        with self._lock:
            with self._cross_process_lock():
                tasks = self._read_all()
                idx = self._find_task_index(tasks, task_id)
                if idx < 0:
                    return False
                task = tasks[idx]
                if task.status != SCHEDULED:
                    return False
                task.status = CANCELLED
                task.executed_at = time.time()
                tasks[idx] = task
                self._write_all(tasks)
                return True

    def cleanup_old(self, now_ts: float | None = None, retention_seconds: float = 86400.0) -> int:
        now_value = float(now_ts if now_ts is not None else time.time())
        retention = max(1.0, float(retention_seconds))
        with self._lock:
            with self._cross_process_lock():
                tasks = self._read_all()
                kept: list[DelayedTask] = []
                removed = 0
                for task in tasks:
                    if task.status not in TERMINAL_STATUSES:
                        kept.append(task)
                        continue
                    baseline = float(task.executed_at if task.executed_at is not None else task.created_at)
                    if now_value - baseline <= retention:
                        kept.append(task)
                        continue
                    removed += 1
                if removed > 0:
                    self._write_all(kept)
                return removed
