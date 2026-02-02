"""Lightweight config loader with periodic reload."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import yaml


class ConfigManager:
    def __init__(self, config_path: str | Path, reload_interval: int = 60) -> None:
        self._path = Path(config_path)
        self._reload_interval = reload_interval
        self._lock = threading.Lock()
        self._last_load = 0.0
        self._data: dict[str, Any] = {}
        self.reload()

    def get(self) -> dict[str, Any]:
        self._reload_if_needed()
        with self._lock:
            return dict(self._data)

    def reload(self) -> None:
        with self._lock:
            if not self._path.exists():
                self._data = {}
                self._last_load = time.time()
                return
            self._data = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
            self._last_load = time.time()

    def _reload_if_needed(self) -> None:
        if time.time() - self._last_load < self._reload_interval:
            return
        self.reload()
