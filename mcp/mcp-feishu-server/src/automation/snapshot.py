"""
描述: 自动化快照存储。
主要功能:
    - 记录记录级字段快照
    - 计算变更字段与幂等哈希输入
"""

from __future__ import annotations

import json
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

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _read_data(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        raw = self._path.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _write_data(self, data: dict[str, Any]) -> None:
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, table_id: str, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            data = self._read_data()
            table_bucket = data.get(table_id)
            if not isinstance(table_bucket, dict):
                return None
            record_bucket = table_bucket.get(record_id)
            if not isinstance(record_bucket, dict):
                return None
            fields = record_bucket.get("fields")
            if not isinstance(fields, dict):
                return None
            return fields

    def save(self, table_id: str, record_id: str, fields: dict[str, Any]) -> None:
        with self._lock:
            data = self._read_data()
            table_bucket = data.setdefault(table_id, {})
            if not isinstance(table_bucket, dict):
                table_bucket = {}
                data[table_id] = table_bucket
            table_bucket[record_id] = {
                "fields": _normalize_value(fields),
                "updated_at": _utc_now_iso(),
            }
            self._write_data(data)

    def init_full_snapshot(self, table_id: str, records: dict[str, dict[str, Any]]) -> int:
        with self._lock:
            data = self._read_data()
            table_bucket: dict[str, Any] = {}
            now = _utc_now_iso()
            for record_id, fields in records.items():
                if not record_id or not isinstance(fields, dict):
                    continue
                table_bucket[record_id] = {
                    "fields": _normalize_value(fields),
                    "updated_at": now,
                }
            data[table_id] = table_bucket
            self._write_data(data)
            return len(table_bucket)

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
