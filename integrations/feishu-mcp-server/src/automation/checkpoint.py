"""
描述: 自动化扫描游标存储。
主要功能:
    - 持久化表级扫描检查点
    - 为补偿轮询提供续扫位置
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any


class CheckpointStore:
    """扫描游标存储：table_id -> last_scan_cursor(ms)"""

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

    def get(self, table_id: str) -> int:
        with self._lock:
            data = self._read_data()
            raw = data.get(table_id, 0)
            try:
                return int(raw)
            except (TypeError, ValueError):
                return 0

    def set(self, table_id: str, cursor: int) -> None:
        with self._lock:
            data = self._read_data()
            data[table_id] = int(cursor)
            self._write_data(data)
