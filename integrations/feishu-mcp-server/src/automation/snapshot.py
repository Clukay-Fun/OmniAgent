"""
描述: 自动化快照存储。
主要功能:
    - 记录记录级字段快照
    - 计算变更字段与幂等哈希输入
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    return value


def _is_same_value(left: Any, right: Any) -> bool:
    return _normalize_value(left) == _normalize_value(right)


class SnapshotStore:
    """快照存储：table_id -> record_id -> {fields, updated_at}"""

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
                CREATE TABLE IF NOT EXISTS snapshots (
                    table_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    fields_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (table_id, record_id)
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
            existing = conn.execute("SELECT COUNT(1) FROM snapshots").fetchone()
            if existing and int(existing[0]) > 0:
                return

            for table_id, table_bucket in data.items():
                if not isinstance(table_bucket, dict):
                    continue
                for record_id, record_bucket in table_bucket.items():
                    if not isinstance(record_bucket, dict):
                        continue
                    fields = record_bucket.get("fields")
                    if not isinstance(fields, dict):
                        continue
                    updated_at = str(record_bucket.get("updated_at") or _utc_now_iso())
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO snapshots(table_id, record_id, fields_json, updated_at)
                        VALUES(?, ?, ?, ?)
                        """,
                        (
                            str(table_id or "").strip(),
                            str(record_id or "").strip(),
                            json.dumps(_normalize_value(fields), ensure_ascii=False),
                            updated_at,
                        ),
                    )

    def load(self, table_id: str, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT fields_json FROM snapshots WHERE table_id = ? AND record_id = ? LIMIT 1",
                    (str(table_id or "").strip(), str(record_id or "").strip()),
                ).fetchone()
            if row is None:
                return None
            try:
                fields = json.loads(str(row[0]))
            except json.JSONDecodeError:
                return None
            if not isinstance(fields, dict):
                return None
            return fields

    def save(self, table_id: str, record_id: str, fields: dict[str, Any]) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO snapshots(table_id, record_id, fields_json, updated_at)
                    VALUES(?, ?, ?, ?)
                    """,
                    (
                        str(table_id or "").strip(),
                        str(record_id or "").strip(),
                        json.dumps(_normalize_value(fields), ensure_ascii=False),
                        _utc_now_iso(),
                    ),
                )

    def init_full_snapshot(self, table_id: str, records: dict[str, dict[str, Any]]) -> int:
        with self._lock:
            now = _utc_now_iso()
            normalized_table_id = str(table_id or "").strip()
            inserted = 0
            with self._connect() as conn:
                conn.execute("DELETE FROM snapshots WHERE table_id = ?", (normalized_table_id,))
                for record_id, fields in records.items():
                    normalized_record_id = str(record_id or "").strip()
                    if not normalized_record_id or not isinstance(fields, dict):
                        continue
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO snapshots(table_id, record_id, fields_json, updated_at)
                        VALUES(?, ?, ?, ?)
                        """,
                        (
                            normalized_table_id,
                            normalized_record_id,
                            json.dumps(_normalize_value(fields), ensure_ascii=False),
                            now,
                        ),
                    )
                    inserted += 1
            return inserted

    @staticmethod
    def diff(old_fields: dict[str, Any], new_fields: dict[str, Any]) -> dict[str, Any]:
        old_keys = set(old_fields.keys())
        new_keys = set(new_fields.keys())

        added_keys = sorted(list(new_keys - old_keys))
        removed_keys = sorted(list(old_keys - new_keys))

        changed: dict[str, dict[str, Any]] = {}
        for key in sorted(list(old_keys & new_keys)):
            old_value = old_fields.get(key)
            new_value = new_fields.get(key)
            if _is_same_value(old_value, new_value):
                continue
            changed[key] = {
                "old": old_value,
                "new": new_value,
            }

        for key in added_keys:
            changed[key] = {
                "old": None,
                "new": new_fields.get(key),
            }

        for key in removed_keys:
            changed[key] = {
                "old": old_fields.get(key),
                "new": None,
            }

        return {
            "has_changes": bool(changed),
            "changed": changed,
            "added_keys": added_keys,
            "removed_keys": removed_keys,
        }
