"""
Callback-level deduplication.

用于确保同一个 callback（由 user_id + action + payload hash 组成的 key）
在一个时间窗口内只执行一次，后续重复请求直接短路返回。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)


class CallbackDeduper:
    """In-memory LRU callback deduplicator.

    用法:
        deduper = CallbackDeduper(window_seconds=10)
        key = deduper.build_key(user_id="u1", action="create_record_confirm", payload={...})
        if deduper.is_duplicate(key):
            return  # 短路
        deduper.mark(key)
    """

    def __init__(self, window_seconds: int = 10, max_size: int = 2048) -> None:
        self._window = max(1, window_seconds)
        self._max_size = max(64, max_size)
        self._cache: OrderedDict[str, float] = OrderedDict()

    def build_key(self, *, user_id: str, action: str, payload: dict[str, Any] | None = None) -> str:
        """Build a dedup key from user_id + action + deterministic payload hash."""
        raw = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False, default=str)
        payload_hash = hashlib.md5(raw.encode()).hexdigest()[:12]
        return f"cb:{user_id}:{action}:{payload_hash}"

    def is_duplicate(self, key: str) -> bool:
        """Check if this key was already seen within the dedup window."""
        self._cleanup()
        return key in self._cache

    def mark(self, key: str) -> None:
        """Mark this key as processed."""
        now = time.time()
        self._cache.pop(key, None)
        self._cache[key] = now
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def _cleanup(self) -> None:
        now = time.time()
        cutoff = now - self._window
        while self._cache:
            oldest_key = next(iter(self._cache))
            if self._cache[oldest_key] < cutoff:
                self._cache.pop(oldest_key)
            else:
                break
