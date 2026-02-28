"""
描述: 自动化扫描游标存储。
主要功能:
    - 持久化表级扫描检查点
    - 为补偿轮询提供续扫位置
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any


class CheckpointStore:
    """扫描游标存储：table_id -> last_scan_cursor(ms)"""

    def __init__(self, path: Path, db_path: Path | None = None) -> None:
        self._legacy_path = path
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
                CREATE TABLE IF NOT EXISTS checkpoints (
                    table_id TEXT PRIMARY KEY,
                    cursor INTEGER NOT NULL
                )
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

        with self._connect() as conn:
            existing = conn.execute("SELECT COUNT(1) FROM checkpoints").fetchone()
            if existing and int(existing[0]) > 0:
                return
            for table_id, value in data.items():
                normalized_table_id = str(table_id or "").strip()
                if not normalized_table_id:
                    continue
                try:
                    cursor = int(value)
                except (TypeError, ValueError):
                    cursor = 0
                conn.execute(
                    "INSERT OR REPLACE INTO checkpoints(table_id, cursor) VALUES(?, ?)",
                    (normalized_table_id, cursor),
                )

    def get(self, table_id: str) -> int:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT cursor FROM checkpoints WHERE table_id = ? LIMIT 1",
                    (str(table_id or "").strip(),),
                ).fetchone()
            if row is None:
                return 0
            return int(row[0])

    def set(self, table_id: str, cursor: int) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO checkpoints(table_id, cursor) VALUES(?, ?)",
                    (str(table_id or "").strip(), int(cursor)),
                )
