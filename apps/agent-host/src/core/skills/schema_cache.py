from __future__ import annotations

import json
import os
from collections import OrderedDict
from hashlib import sha256
from pathlib import Path
from threading import RLock
from time import time
from typing import Any


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "")


class SchemaCache:
    """Lightweight in-memory schema cache keyed by table_id."""

    def __init__(
        self,
        metadata_path: str | Path | None = None,
        ttl_seconds: int = 600,
        max_tables: int = 20,
        clock: Any | None = None,
    ) -> None:
        self._lock = RLock()
        self._schemas: dict[str, list[dict[str, Any]]] = {}
        self._field_index: dict[str, dict[str, dict[str, Any]]] = {}
        self._expires_at: dict[str, float] = {}
        self._lru_keys: OrderedDict[str, None] = OrderedDict()
        self._ttl_seconds = max(0, int(ttl_seconds))
        self._max_tables = max(1, int(max_tables))
        self._clock = clock or time
        self._metadata_path = self._resolve_metadata_path(metadata_path)
        self._metadata: dict[str, dict[str, Any]] = self._load_metadata()

    def _now(self) -> float:
        value = self._clock()
        return float(value) if isinstance(value, (int, float)) else float(time())

    def _is_expired(self, table_id: str, now_ts: float | None = None) -> bool:
        if self._ttl_seconds <= 0:
            return False
        expire_at = self._expires_at.get(table_id)
        if expire_at is None:
            return False
        check_time = self._now() if now_ts is None else now_ts
        return check_time >= expire_at

    def _drop_runtime_cache(self, table_id: str) -> None:
        self._schemas.pop(table_id, None)
        self._field_index.pop(table_id, None)
        self._expires_at.pop(table_id, None)
        self._lru_keys.pop(table_id, None)

    def _touch_lru(self, table_id: str) -> None:
        self._lru_keys.pop(table_id, None)
        self._lru_keys[table_id] = None

    def _apply_lru_cap(self) -> None:
        while len(self._lru_keys) > self._max_tables:
            stale_table_id, _ = self._lru_keys.popitem(last=False)
            self._schemas.pop(stale_table_id, None)
            self._field_index.pop(stale_table_id, None)
            self._expires_at.pop(stale_table_id, None)

    def _resolve_metadata_path(self, metadata_path: str | Path | None) -> Path | None:
        if metadata_path is not None:
            path = Path(metadata_path)
            return path if str(path).strip() else None
        env_path = os.getenv("SCHEMA_CACHE_METADATA_PATH", "").strip()
        if env_path:
            return Path(env_path)
        default_path = Path("workspace/cache/schema_metadata.json")
        return default_path

    def _load_metadata(self) -> dict[str, dict[str, Any]]:
        path = self._metadata_path
        if path is None or not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        output: dict[str, dict[str, Any]] = {}
        for key, value in raw.items():
            table_id = str(key or "").strip()
            if not table_id or not isinstance(value, dict):
                continue
            output[table_id] = dict(value)
        return output

    def _persist_metadata(self) -> None:
        path = self._metadata_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._metadata, ensure_ascii=False, sort_keys=True, indent=2),
                encoding="utf-8",
            )
        except Exception:
            return

    def _schema_hash(self, schema: list[dict[str, Any]]) -> str:
        payload = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()

    def get_schema(self, table_id: str) -> list[dict[str, Any]] | None:
        table_key = str(table_id or "").strip()
        if not table_key:
            return None
        with self._lock:
            if self._is_expired(table_key):
                self._drop_runtime_cache(table_key)
                return None
            schema = self._schemas.get(table_key)
            if schema is None:
                return None
            self._touch_lru(table_key)
            return [dict(item) for item in schema]

    def set_schema(self, table_id: str, schema: Any) -> None:
        table_key = str(table_id or "").strip()
        if not table_key:
            return
        if not isinstance(schema, list):
            return

        sanitized: list[dict[str, Any]] = []
        index: dict[str, dict[str, Any]] = {}
        for item in schema:
            if not isinstance(item, dict):
                continue
            meta = dict(item)
            sanitized.append(meta)
            for key_name in ("field_id", "id", "name", "field_name", "title", "label"):
                key_value = _normalize_key(meta.get(key_name))
                if key_value:
                    index[key_value] = meta

        with self._lock:
            self._schemas[table_key] = sanitized
            self._field_index[table_key] = index
            now_ts = self._now()
            if self._ttl_seconds > 0:
                self._expires_at[table_key] = now_ts + self._ttl_seconds
            else:
                self._expires_at.pop(table_key, None)
            self._touch_lru(table_key)
            self._apply_lru_cap()
            self._metadata[table_key] = {
                "updated_at": int(now_ts),
                "schema_hash": self._schema_hash(sanitized),
                "field_count": len(sanitized),
            }
            self._persist_metadata()

    def invalidate(self, table_id: str) -> None:
        table_key = str(table_id or "").strip()
        if not table_key:
            return
        with self._lock:
            self._drop_runtime_cache(table_key)

    def refresh(self, table_id: str) -> None:
        """Manual refresh entrypoint: invalidate local runtime cache by table_id."""
        self.invalidate(table_id)

    def get_metadata(self, table_id: str) -> dict[str, Any] | None:
        table_key = str(table_id or "").strip()
        if not table_key:
            return None
        with self._lock:
            meta = self._metadata.get(table_key)
            if not isinstance(meta, dict):
                return None
            return dict(meta)

    def get_field_meta(self, table_id: str, field_name_or_id: str) -> dict[str, Any] | None:
        table_key = str(table_id or "").strip()
        lookup_key = _normalize_key(field_name_or_id)
        if not table_key or not lookup_key:
            return None
        with self._lock:
            if self._is_expired(table_key):
                self._drop_runtime_cache(table_key)
                return None
            table_index = self._field_index.get(table_key)
            if not table_index:
                return None
            self._touch_lru(table_key)
            meta = table_index.get(lookup_key)
            if meta is None:
                return None
            return dict(meta)


_GLOBAL_SCHEMA_CACHE = SchemaCache()


def get_global_schema_cache() -> SchemaCache:
    return _GLOBAL_SCHEMA_CACHE
