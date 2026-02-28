"""
描述: 自动化幂等存储。
主要功能:
    - 管理事件级与业务级去重键
    - 定期清理过期键并控制容量
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import Any


class IdempotencyStore:
    """幂等存储：事件级去重 + 业务级去重。"""

    def __init__(
        self,
        path: Path,
        event_ttl_seconds: int = 604800,
        business_ttl_seconds: int = 604800,
        max_keys: int = 50000,
        db_path: Path | None = None,
    ) -> None:
        self._legacy_path = path
        self._event_ttl_seconds = max(1, int(event_ttl_seconds))
        self._business_ttl_seconds = max(1, int(business_ttl_seconds))
        self._max_keys = max(100, int(max_keys))
        self._lock = Lock()
        self._legacy_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path if db_path is not None else self._legacy_path.parent / "automation.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._migrate_legacy_if_needed()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    bucket TEXT NOT NULL,
                    key TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    PRIMARY KEY (bucket, key)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_idempotency_bucket_ts
                ON idempotency_keys (bucket, ts)
                """
            )

    def _migrate_legacy_if_needed(self) -> None:
        if not self._legacy_path.exists():
            return
        raw = self._legacy_path.read_text(encoding="utf-8").strip()
        if not raw:
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return

        events_raw = data.get("events")
        events: dict[str, Any] = events_raw if isinstance(events_raw, dict) else {}
        business_raw = data.get("business")
        business: dict[str, Any] = business_raw if isinstance(business_raw, dict) else {}
        with self._connect() as conn:
            existing = conn.execute("SELECT COUNT(1) FROM idempotency_keys").fetchone()
            if existing and int(existing[0]) > 0:
                return
            for bucket_name, bucket_data in (("events", events), ("business", business)):
                if not isinstance(bucket_data, dict):
                    continue
                for key, value in bucket_data.items():
                    try:
                        ts = int(value)
                    except (TypeError, ValueError):
                        continue
                    normalized_key = str(key or "").strip()
                    if not normalized_key:
                        continue
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO idempotency_keys(bucket, key, ts)
                        VALUES(?, ?, ?)
                        """,
                        (bucket_name, normalized_key, ts),
                    )

    @staticmethod
    def _now_ts() -> int:
        return int(time.time())

    def _cleanup_expired(self, conn: sqlite3.Connection, bucket: str, ttl_seconds: int, now_ts: int) -> None:
        conn.execute(
            "DELETE FROM idempotency_keys WHERE bucket = ? AND ts < ?",
            (bucket, now_ts - int(ttl_seconds)),
        )

    def _cleanup_oversized(self, conn: sqlite3.Connection, bucket: str) -> None:
        conn.execute(
            """
            DELETE FROM idempotency_keys
            WHERE bucket = ?
              AND key IN (
                SELECT key
                FROM idempotency_keys
                WHERE bucket = ?
                ORDER BY ts DESC
                LIMIT -1 OFFSET ?
              )
            """,
            (bucket, bucket, int(self._max_keys)),
        )

    def _cleanup_all(self, conn: sqlite3.Connection, now_ts: int) -> None:
        self._cleanup_expired(conn, "events", self._event_ttl_seconds, now_ts)
        self._cleanup_expired(conn, "business", self._business_ttl_seconds, now_ts)
        self._cleanup_oversized(conn, "events")
        self._cleanup_oversized(conn, "business")

    def cleanup(self) -> None:
        with self._lock:
            now_ts = self._now_ts()
            with self._connect() as conn:
                self._cleanup_all(conn, now_ts)

    def is_event_duplicate(self, event_key: str) -> bool:
        if not event_key:
            return False
        with self._lock:
            now_ts = self._now_ts()
            normalized = str(event_key).strip()
            if not normalized:
                return False
            with self._connect() as conn:
                self._cleanup_all(conn, now_ts)
                row = conn.execute(
                    "SELECT 1 FROM idempotency_keys WHERE bucket = 'events' AND key = ? LIMIT 1",
                    (normalized,),
                ).fetchone()
                return bool(row)

    def mark_event(self, event_key: str) -> None:
        if not event_key:
            return
        with self._lock:
            now_ts = self._now_ts()
            normalized = str(event_key).strip()
            if not normalized:
                return
            with self._connect() as conn:
                self._cleanup_all(conn, now_ts)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO idempotency_keys(bucket, key, ts)
                    VALUES('events', ?, ?)
                    """,
                    (normalized, now_ts),
                )
                self._cleanup_oversized(conn, "events")

    def is_business_duplicate(self, business_key: str) -> bool:
        if not business_key:
            return False
        with self._lock:
            now_ts = self._now_ts()
            normalized = str(business_key).strip()
            if not normalized:
                return False
            with self._connect() as conn:
                self._cleanup_all(conn, now_ts)
                row = conn.execute(
                    "SELECT 1 FROM idempotency_keys WHERE bucket = 'business' AND key = ? LIMIT 1",
                    (normalized,),
                ).fetchone()
                return bool(row)

    def mark_business(self, business_key: str) -> None:
        if not business_key:
            return
        with self._lock:
            now_ts = self._now_ts()
            normalized = str(business_key).strip()
            if not normalized:
                return
            with self._connect() as conn:
                self._cleanup_all(conn, now_ts)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO idempotency_keys(bucket, key, ts)
                    VALUES('business', ?, ?)
                    """,
                    (normalized, now_ts),
                )
                self._cleanup_oversized(conn, "business")
