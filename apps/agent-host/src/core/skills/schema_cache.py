from __future__ import annotations

from threading import RLock
from typing import Any


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "")


class SchemaCache:
    """Lightweight in-memory schema cache keyed by table_id."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._schemas: dict[str, list[dict[str, Any]]] = {}
        self._field_index: dict[str, dict[str, dict[str, Any]]] = {}

    def get_schema(self, table_id: str) -> list[dict[str, Any]] | None:
        table_key = str(table_id or "").strip()
        if not table_key:
            return None
        with self._lock:
            schema = self._schemas.get(table_key)
            if schema is None:
                return None
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

    def invalidate(self, table_id: str) -> None:
        table_key = str(table_id or "").strip()
        if not table_key:
            return
        with self._lock:
            self._schemas.pop(table_key, None)
            self._field_index.pop(table_key, None)

    def get_field_meta(self, table_id: str, field_name_or_id: str) -> dict[str, Any] | None:
        table_key = str(table_id or "").strip()
        lookup_key = _normalize_key(field_name_or_id)
        if not table_key or not lookup_key:
            return None
        with self._lock:
            table_index = self._field_index.get(table_key)
            if not table_index:
                return None
            meta = table_index.get(lookup_key)
            if meta is None:
                return None
            return dict(meta)


_GLOBAL_SCHEMA_CACHE = SchemaCache()


def get_global_schema_cache() -> SchemaCache:
    return _GLOBAL_SCHEMA_CACHE
