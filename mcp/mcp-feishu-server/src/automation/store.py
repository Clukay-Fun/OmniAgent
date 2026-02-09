from __future__ import annotations

import json
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
    ) -> None:
        self._path = path
        self._event_ttl_seconds = max(1, int(event_ttl_seconds))
        self._business_ttl_seconds = max(1, int(business_ttl_seconds))
        self._max_keys = max(100, int(max_keys))
        self._lock = Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _read_data(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"events": {}, "business": {}}
        raw = self._path.read_text(encoding="utf-8").strip()
        if not raw:
            return {"events": {}, "business": {}}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {"events": {}, "business": {}}
        if not isinstance(data, dict):
            return {"events": {}, "business": {}}
        events = data.get("events")
        business = data.get("business")
        if not isinstance(events, dict):
            events = {}
        if not isinstance(business, dict):
            business = {}
        return {"events": events, "business": business}

    def _write_data(self, data: dict[str, Any]) -> None:
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _now_ts() -> int:
        return int(time.time())

    def _evict_expired(self, bucket: dict[str, Any], ttl_seconds: int, now_ts: int) -> dict[str, int]:
        result: dict[str, int] = {}
        for key, value in bucket.items():
            try:
                ts = int(value)
            except (TypeError, ValueError):
                continue
            if now_ts - ts > ttl_seconds:
                continue
            result[key] = ts
        return result

    def _evict_oversized(self, bucket: dict[str, int]) -> dict[str, int]:
        if len(bucket) <= self._max_keys:
            return bucket
        sorted_items = sorted(bucket.items(), key=lambda item: item[1], reverse=True)
        return dict(sorted_items[: self._max_keys])

    def cleanup(self) -> None:
        with self._lock:
            now_ts = self._now_ts()
            data = self._read_data()
            events = self._evict_expired(data.get("events", {}), self._event_ttl_seconds, now_ts)
            business = self._evict_expired(data.get("business", {}), self._business_ttl_seconds, now_ts)
            data["events"] = self._evict_oversized(events)
            data["business"] = self._evict_oversized(business)
            self._write_data(data)

    def is_event_duplicate(self, event_key: str) -> bool:
        if not event_key:
            return False
        with self._lock:
            now_ts = self._now_ts()
            data = self._read_data()
            events = self._evict_expired(data.get("events", {}), self._event_ttl_seconds, now_ts)
            duplicate = event_key in events
            data["events"] = self._evict_oversized(events)
            data["business"] = self._evict_oversized(
                self._evict_expired(data.get("business", {}), self._business_ttl_seconds, now_ts)
            )
            self._write_data(data)
            return duplicate

    def mark_event(self, event_key: str) -> None:
        if not event_key:
            return
        with self._lock:
            now_ts = self._now_ts()
            data = self._read_data()
            events = self._evict_expired(data.get("events", {}), self._event_ttl_seconds, now_ts)
            business = self._evict_expired(data.get("business", {}), self._business_ttl_seconds, now_ts)
            events[event_key] = now_ts
            data["events"] = self._evict_oversized(events)
            data["business"] = self._evict_oversized(business)
            self._write_data(data)

    def is_business_duplicate(self, business_key: str) -> bool:
        if not business_key:
            return False
        with self._lock:
            now_ts = self._now_ts()
            data = self._read_data()
            events = self._evict_expired(data.get("events", {}), self._event_ttl_seconds, now_ts)
            business = self._evict_expired(data.get("business", {}), self._business_ttl_seconds, now_ts)
            duplicate = business_key in business
            data["events"] = self._evict_oversized(events)
            data["business"] = self._evict_oversized(business)
            self._write_data(data)
            return duplicate

    def mark_business(self, business_key: str) -> None:
        if not business_key:
            return
        with self._lock:
            now_ts = self._now_ts()
            data = self._read_data()
            events = self._evict_expired(data.get("events", {}), self._event_ttl_seconds, now_ts)
            business = self._evict_expired(data.get("business", {}), self._business_ttl_seconds, now_ts)
            business[business_key] = now_ts
            data["events"] = self._evict_oversized(events)
            data["business"] = self._evict_oversized(business)
            self._write_data(data)
