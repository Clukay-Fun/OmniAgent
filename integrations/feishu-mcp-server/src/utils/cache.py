"""
描述: 简单 TTL + LRU 缓存
主要功能:
    - 基于过期时间的缓存
    - 超出容量自动淘汰最久未使用项
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    def __init__(self, max_size: int = 128, ttl_seconds: int = 600) -> None:
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._store: OrderedDict[str, tuple[Any, float]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        item = self._store.get(key)
        if not item:
            return None
        value, expires_at = item
        if expires_at < time.time():
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        expires_at = time.time() + self._ttl
        if key in self._store:
            self._store.pop(key, None)
        self._store[key] = (value, expires_at)
        self._store.move_to_end(key)
        self._evict()

    def clear(self) -> None:
        self._store.clear()

    def _evict(self) -> None:
        while len(self._store) > self._max_size:
            self._store.popitem(last=False)
