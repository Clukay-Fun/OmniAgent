"""
描述: 自动化运行日志存储。
主要功能:
    - 以 JSONL 方式持久化执行日志
    - 提供统一时间戳补全
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class RunLogStore:
    """运行日志：JSONL 追加写入。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(tz=timezone.utc).isoformat()

    def write(self, payload: dict[str, Any]) -> None:
        entry = dict(payload)
        entry.setdefault("timestamp", self._utc_now_iso())
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fp:
                fp.write(line + "\n")
