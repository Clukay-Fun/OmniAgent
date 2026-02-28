"""
描述: 自动化死信存储。
主要功能:
    - 记录动作失败后的死信条目（SQLite）
    - 统一保存失败上下文用于排障
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class DeadLetterStore:
    """死信记录：SQLite 存储。"""

    def __init__(self, db_path: Path) -> None:
        self._lock = Lock()
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

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
                CREATE TABLE IF NOT EXISTS dead_letters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_dead_letters_timestamp
                ON dead_letters (timestamp)
                """
            )

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(tz=timezone.utc).isoformat()

    def write(self, payload: dict[str, Any]) -> None:
        entry = dict(payload)
        entry.setdefault("timestamp", self._utc_now_iso())
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO dead_letters(timestamp, payload_json) VALUES(?, ?)",
                    (str(entry.get("timestamp") or self._utc_now_iso()), line),
                )
