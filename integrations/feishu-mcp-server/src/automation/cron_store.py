"""
描述: cron 周期任务存储。
主要功能:
    - 使用 SQLite 持久化 cron 任务队列
    - 提供状态迁移与并发安全的领取能力
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3
from threading import Lock
import time
from typing import Any


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
    """Cron 任务存储（SQLite + 进程内锁）。"""

    def __init__(self, file_path: str | Path = "automation_data/cron_queue.jsonl", db_path: str | Path | None = None) -> None:
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
                CREATE TABLE IF NOT EXISTS cron_jobs (
                    job_id TEXT PRIMARY KEY,
                    cron_expr TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    next_run_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_run_at REAL,
                    last_success_at REAL,
                    last_failure_at REAL,
                    last_error TEXT,
                    pause_reason TEXT,
                    paused_at REAL,
                    cancelled_at REAL,
                    consecutive_failures INTEGER NOT NULL,
                    max_consecutive_failures INTEGER NOT NULL,
                    execution_count INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cron_jobs_due
                ON cron_jobs (status, next_run_at, created_at, job_id)
                """
            )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> CronJob:
        payload: dict[str, Any] = {}
        try:
            loaded = json.loads(str(row["payload_json"]))
            if isinstance(loaded, dict):
                payload = loaded
        except json.JSONDecodeError:
            payload = {}
        return CronJob(
            job_id=str(row["job_id"]),
            cron_expr=str(row["cron_expr"]),
            payload=payload,
            rule_id=str(row["rule_id"]),
            status=str(row["status"]),
            next_run_at=float(row["next_run_at"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            last_run_at=(float(row["last_run_at"]) if row["last_run_at"] is not None else None),
            last_success_at=(float(row["last_success_at"]) if row["last_success_at"] is not None else None),
            last_failure_at=(float(row["last_failure_at"]) if row["last_failure_at"] is not None else None),
            last_error=(str(row["last_error"]) if row["last_error"] is not None else None),
            pause_reason=(str(row["pause_reason"]) if row["pause_reason"] is not None else None),
            paused_at=(float(row["paused_at"]) if row["paused_at"] is not None else None),
            cancelled_at=(float(row["cancelled_at"]) if row["cancelled_at"] is not None else None),
            consecutive_failures=int(row["consecutive_failures"]),
            max_consecutive_failures=max(1, int(row["max_consecutive_failures"])),
            execution_count=int(row["execution_count"]),
        )

    def _migrate_legacy_if_needed(self) -> None:
        if not self._legacy_file_path.exists():
            return
        raw = self._legacy_file_path.read_text(encoding="utf-8")
        if not raw.strip():
            return

        with self._connect() as conn:
            existing = conn.execute("SELECT COUNT(1) FROM cron_jobs").fetchone()
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
                job = CronJob.from_dict(parsed)
                if not job.job_id or not job.cron_expr:
                    continue
                if job.status not in VALID_CRON_STATUSES:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO cron_jobs(
                        job_id, cron_expr, payload_json, rule_id, status, next_run_at, created_at, updated_at,
                        last_run_at, last_success_at, last_failure_at, last_error, pause_reason, paused_at,
                        cancelled_at, consecutive_failures, max_consecutive_failures, execution_count
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.job_id,
                        job.cron_expr,
                        json.dumps(job.payload, ensure_ascii=False),
                        job.rule_id,
                        job.status,
                        float(job.next_run_at),
                        float(job.created_at),
                        float(job.updated_at),
                        job.last_run_at,
                        job.last_success_at,
                        job.last_failure_at,
                        job.last_error,
                        job.pause_reason,
                        job.paused_at,
                        job.cancelled_at,
                        int(job.consecutive_failures),
                        int(job.max_consecutive_failures),
                        int(job.execution_count),
                    ),
                )

    def list_jobs(self) -> list[CronJob]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM cron_jobs
                    ORDER BY created_at ASC, job_id ASC
                    """
                ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def get_job(self, job_id: str) -> CronJob | None:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return None

        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM cron_jobs WHERE job_id = ? LIMIT 1",
                    (normalized_job_id,),
                ).fetchone()
                if row is None:
                    return None
                return self._row_to_job(row)

    def schedule(self, job: CronJob) -> None:
        if not job.job_id:
            raise ValueError("job_id is required")
        if not job.cron_expr:
            raise ValueError("cron_expr is required")

        with self._lock:
            with self._connect() as conn:
                try:
                    conn.execute(
                        """
                        INSERT INTO cron_jobs(
                            job_id, cron_expr, payload_json, rule_id, status, next_run_at, created_at, updated_at,
                            last_run_at, last_success_at, last_failure_at, last_error, pause_reason, paused_at,
                            cancelled_at, consecutive_failures, max_consecutive_failures, execution_count
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            job.job_id,
                            job.cron_expr,
                            json.dumps(job.payload, ensure_ascii=False),
                            job.rule_id,
                            job.status,
                            float(job.next_run_at),
                            float(job.created_at),
                            float(job.updated_at),
                            job.last_run_at,
                            job.last_success_at,
                            job.last_failure_at,
                            job.last_error,
                            job.pause_reason,
                            job.paused_at,
                            job.cancelled_at,
                            int(job.consecutive_failures),
                            int(job.max_consecutive_failures),
                            int(job.execution_count),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError(f"duplicate cron job id: {job.job_id}") from exc

    def activate_waiting(self, now_ts: float | None = None) -> int:
        now_value = float(now_ts if now_ts is not None else time.time())
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    UPDATE cron_jobs
                    SET status = ?, updated_at = ?
                    WHERE status = ? AND next_run_at <= ?
                    """,
                    (ACTIVE, now_value, WAITING, now_value),
                )
                return int(cur.rowcount)

    def acquire_due_jobs(self, now_ts: float | None = None, limit: int = 100) -> list[CronJob]:
        now_value = float(now_ts if now_ts is not None else time.time())
        max_items = max(1, int(limit))
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    """
                    SELECT job_id
                    FROM cron_jobs
                    WHERE status = ? AND next_run_at <= ?
                    ORDER BY next_run_at ASC, created_at ASC, job_id ASC
                    LIMIT ?
                    """,
                    (ACTIVE, now_value, max_items),
                ).fetchall()
                if not rows:
                    conn.commit()
                    return []
                job_ids = [str(row["job_id"]) for row in rows]
                for job_id in job_ids:
                    conn.execute(
                        """
                        UPDATE cron_jobs
                        SET status = ?, last_run_at = ?, updated_at = ?
                        WHERE job_id = ? AND status = ?
                        """,
                        (EXECUTING, now_value, now_value, job_id, ACTIVE),
                    )
                placeholders = ",".join("?" for _ in job_ids)
                acquired_rows = conn.execute(
                    f"SELECT * FROM cron_jobs WHERE job_id IN ({placeholders}) ORDER BY next_run_at ASC, created_at ASC, job_id ASC",
                    tuple(job_ids),
                ).fetchall()
                conn.commit()
        return [self._row_to_job(row) for row in acquired_rows if str(row["status"]) == EXECUTING]

    def mark_success(self, job_id: str, *, next_run_at: float, executed_at: float | None = None) -> bool:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return False

        now_value = float(executed_at if executed_at is not None else time.time())
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    UPDATE cron_jobs
                    SET status = ?, next_run_at = ?, last_success_at = ?,
                        last_error = NULL, pause_reason = NULL, paused_at = NULL,
                        consecutive_failures = 0,
                        execution_count = execution_count + 1,
                        updated_at = ?
                    WHERE job_id = ? AND status = ?
                    """,
                    (WAITING, float(next_run_at), now_value, now_value, normalized_job_id, EXECUTING),
                )
                return cur.rowcount > 0

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
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT status, consecutive_failures, max_consecutive_failures
                    FROM cron_jobs
                    WHERE job_id = ?
                    LIMIT 1
                    """,
                    (normalized_job_id,),
                ).fetchone()
                if row is None:
                    return {"updated": False, "paused": False}

                current_status = str(row["status"])
                if current_status != EXECUTING:
                    return {"updated": False, "paused": False, "current_status": current_status}

                threshold = max(1, int(row["max_consecutive_failures"] or max_consecutive_failures or 3))
                next_failures = int(row["consecutive_failures"]) + 1
                paused = next_failures >= threshold

                if paused:
                    conn.execute(
                        """
                        UPDATE cron_jobs
                        SET status = ?, last_failure_at = ?, last_error = ?,
                            execution_count = execution_count + 1,
                            consecutive_failures = ?, max_consecutive_failures = ?,
                            paused_at = ?, pause_reason = ?, updated_at = ?
                        WHERE job_id = ? AND status = ?
                        """,
                        (
                            PAUSED,
                            now_value,
                            str(detail or ""),
                            next_failures,
                            threshold,
                            now_value,
                            f"consecutive failures reached {threshold}",
                            now_value,
                            normalized_job_id,
                            EXECUTING,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE cron_jobs
                        SET status = ?, next_run_at = ?,
                            last_failure_at = ?, last_error = ?,
                            execution_count = execution_count + 1,
                            consecutive_failures = ?, max_consecutive_failures = ?,
                            updated_at = ?
                        WHERE job_id = ? AND status = ?
                        """,
                        (
                            WAITING,
                            float(next_run_at),
                            now_value,
                            str(detail or ""),
                            next_failures,
                            threshold,
                            now_value,
                            normalized_job_id,
                            EXECUTING,
                        ),
                    )
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
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT status, next_run_at FROM cron_jobs WHERE job_id = ? LIMIT 1",
                    (normalized_job_id,),
                ).fetchone()
                if row is None:
                    return False
                if str(row["status"]) != PAUSED:
                    return False
                next_run_at = max(float(row["next_run_at"]), now_value)
                cur = conn.execute(
                    """
                    UPDATE cron_jobs
                    SET status = ?, paused_at = NULL, pause_reason = NULL,
                        consecutive_failures = 0, next_run_at = ?, updated_at = ?
                    WHERE job_id = ? AND status = ?
                    """,
                    (ACTIVE, next_run_at, now_value, normalized_job_id, PAUSED),
                )
                return cur.rowcount > 0

    def cancel(self, job_id: str, now_ts: float | None = None) -> bool:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return False

        now_value = float(now_ts if now_ts is not None else time.time())
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    UPDATE cron_jobs
                    SET status = ?, cancelled_at = ?, updated_at = ?
                    WHERE job_id = ? AND status NOT IN (?, ?)
                    """,
                    (CANCELLED, now_value, now_value, normalized_job_id, CANCELLED, EXECUTING),
                )
                return cur.rowcount > 0
