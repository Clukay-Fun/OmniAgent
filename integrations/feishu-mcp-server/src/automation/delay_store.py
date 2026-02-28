"""
描述: delay 动作任务存储。
主要功能:
    - 使用 SQLite 持久化延迟任务队列
    - 提供状态迁移与到期任务读取能力
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3
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
    """延迟任务存储（SQLite + 进程内锁）。"""

    def __init__(self, file_path: str | Path = "automation_data/delay_queue.jsonl", db_path: str | Path | None = None) -> None:
        self._legacy_file_path = Path(file_path)
        self._legacy_file_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = Path(db_path) if db_path is not None else self._legacy_file_path.parent / "automation.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._init_db()
        self._migrate_legacy_if_needed()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS delay_tasks (
                    task_id TEXT PRIMARY KEY,
                    rule_id TEXT NOT NULL,
                    trigger_at REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    executed_at REAL,
                    error_detail TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_delay_tasks_due
                ON delay_tasks (status, trigger_at, created_at, task_id)
                """
            )

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> DelayedTask:
        payload: dict[str, Any] = {}
        try:
            loaded = json.loads(str(row["payload_json"]))
            if isinstance(loaded, dict):
                payload = loaded
        except json.JSONDecodeError:
            payload = {}
        return DelayedTask(
            task_id=str(row["task_id"]),
            rule_id=str(row["rule_id"]),
            trigger_at=float(row["trigger_at"]),
            payload=payload,
            status=str(row["status"]),
            created_at=float(row["created_at"]),
            executed_at=(float(row["executed_at"]) if row["executed_at"] is not None else None),
            error_detail=(str(row["error_detail"]) if row["error_detail"] is not None else None),
        )

    def _migrate_legacy_if_needed(self) -> None:
        if not self._legacy_file_path.exists():
            return
        raw = self._legacy_file_path.read_text(encoding="utf-8")
        if not raw.strip():
            return

        with self._connect() as conn:
            existing = conn.execute("SELECT COUNT(1) FROM delay_tasks").fetchone()
            if existing and int(existing[0]) > 0:
                return
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
                conn.execute(
                    """
                    INSERT OR IGNORE INTO delay_tasks(
                        task_id, rule_id, trigger_at, payload_json, status, created_at, executed_at, error_detail
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.task_id,
                        task.rule_id,
                        float(task.trigger_at),
                        json.dumps(task.payload, ensure_ascii=False),
                        task.status,
                        float(task.created_at),
                        task.executed_at,
                        task.error_detail,
                    ),
                )

    def list_tasks(self) -> list[DelayedTask]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT task_id, rule_id, trigger_at, payload_json, status, created_at, executed_at, error_detail
                    FROM delay_tasks
                    ORDER BY created_at ASC, task_id ASC
                    """
                ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def schedule(self, task: DelayedTask) -> None:
        if not task.task_id:
            raise ValueError("task_id is required")
        with self._lock:
            with self._connect() as conn:
                try:
                    conn.execute(
                        """
                        INSERT INTO delay_tasks(
                            task_id, rule_id, trigger_at, payload_json, status, created_at, executed_at, error_detail
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task.task_id,
                            task.rule_id,
                            float(task.trigger_at),
                            json.dumps(task.payload, ensure_ascii=False),
                            task.status,
                            float(task.created_at),
                            task.executed_at,
                            task.error_detail,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError(f"duplicate delay task id: {task.task_id}") from exc

    def get_due_tasks(self, now_ts: float | None = None, limit: int = 100) -> list[DelayedTask]:
        now_value = float(now_ts if now_ts is not None else time.time())
        max_items = max(1, int(limit))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT task_id, rule_id, trigger_at, payload_json, status, created_at, executed_at, error_detail
                    FROM delay_tasks
                    WHERE status = ? AND trigger_at <= ?
                    ORDER BY trigger_at ASC, created_at ASC, task_id ASC
                    LIMIT ?
                    """,
                    (SCHEDULED, now_value, max_items),
                ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def mark_executing(self, task_id: str) -> bool:
        if not task_id:
            return False
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    UPDATE delay_tasks
                    SET status = ?, error_detail = NULL
                    WHERE task_id = ? AND status = ?
                    """,
                    (EXECUTING, str(task_id), SCHEDULED),
                )
                return cur.rowcount > 0

    def mark_completed(self, task_id: str) -> bool:
        if not task_id:
            return False
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    UPDATE delay_tasks
                    SET status = ?, executed_at = ?, error_detail = NULL
                    WHERE task_id = ? AND status IN (?, ?)
                    """,
                    (COMPLETED, float(time.time()), str(task_id), SCHEDULED, EXECUTING),
                )
                return cur.rowcount > 0

    def mark_failed(self, task_id: str, detail: str) -> bool:
        if not task_id:
            return False
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    UPDATE delay_tasks
                    SET status = ?, executed_at = ?, error_detail = ?
                    WHERE task_id = ? AND status IN (?, ?)
                    """,
                    (FAILED, float(time.time()), str(detail or ""), str(task_id), SCHEDULED, EXECUTING),
                )
                return cur.rowcount > 0

    def cancel(self, task_id: str) -> bool:
        if not task_id:
            return False
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    UPDATE delay_tasks
                    SET status = ?, executed_at = ?
                    WHERE task_id = ? AND status = ?
                    """,
                    (CANCELLED, float(time.time()), str(task_id), SCHEDULED),
                )
                return cur.rowcount > 0

    def cleanup_old(self, now_ts: float | None = None, retention_seconds: float = 86400.0) -> int:
        now_value = float(now_ts if now_ts is not None else time.time())
        retention = max(1.0, float(retention_seconds))
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    DELETE FROM delay_tasks
                    WHERE status IN (?, ?, ?)
                      AND (? - COALESCE(executed_at, created_at)) > ?
                    """,
                    (COMPLETED, FAILED, CANCELLED, now_value, retention),
                )
                return int(cur.rowcount)
