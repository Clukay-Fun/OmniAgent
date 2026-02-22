"""SQLite-backed lightweight mid-term memory persistence."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_KEYWORD_PATTERN = re.compile(r"[A-Za-z0-9_\-\u4e00-\u9fff]{2,}")


@dataclass(frozen=True)
class MidtermMemoryItem:
    kind: str
    value: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


class RuleSummaryExtractor:
    """Build low-cost summary items from request/result."""

    _STOPWORDS = {
        "请问",
        "帮我",
        "一下",
        "这个",
        "那个",
        "我们",
        "你们",
        "他们",
    }

    def __init__(self, max_keywords: int = 5) -> None:
        self._max_keywords = max(1, max_keywords)

    def build_items(
        self,
        user_text: str,
        skill_name: str,
        result_data: dict[str, Any] | None,
    ) -> list[MidtermMemoryItem]:
        items: list[MidtermMemoryItem] = []
        items.extend(self._keyword_items(user_text))

        result_payload = result_data if isinstance(result_data, dict) else {}
        event_meta: dict[str, Any] = {"skill_name": skill_name}
        if "total" in result_payload:
            event_meta["total"] = result_payload.get("total")
        if result_payload.get("record_id"):
            event_meta["record_id"] = str(result_payload.get("record_id"))

        items.append(
            MidtermMemoryItem(
                kind="event",
                value=f"skill:{skill_name or 'unknown'}",
                source="orchestrator",
                metadata=event_meta,
            )
        )
        return items

    def _keyword_items(self, user_text: str) -> list[MidtermMemoryItem]:
        seen: set[str] = set()
        keywords: list[str] = []
        for match in _KEYWORD_PATTERN.findall(user_text or ""):
            token = match.strip().lower()
            if len(token) < 2 or token in self._STOPWORDS or token in seen:
                continue
            seen.add(token)
            keywords.append(token)
            if len(keywords) >= self._max_keywords:
                break

        return [
            MidtermMemoryItem(
                kind="keyword",
                value=keyword,
                source="orchestrator",
                metadata={"from": "user_text"},
            )
            for keyword in keywords
        ]


class SQLiteMidtermMemoryStore:
    """Persist summary items to SQLite for future retrieval."""

    def __init__(self, db_path: str = "workspace/memory/midterm_memory.sqlite3") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))

    def _ensure_schema(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS midterm_memory (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        value TEXT NOT NULL,
                        source TEXT NOT NULL,
                        metadata_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_midterm_memory_user_created
                    ON midterm_memory(user_id, created_at)
                    """
                )

    def write_items(self, user_id: str, items: list[MidtermMemoryItem]) -> int:
        rows = [
            (
                user_id,
                item.kind,
                item.value,
                item.source,
                json.dumps(item.metadata or {}, ensure_ascii=False, sort_keys=True),
                datetime.now(timezone.utc).isoformat(),
            )
            for item in items
            if item.value.strip()
        ]
        if not rows:
            return 0

        with self._lock:
            with self._connect() as conn:
                conn.executemany(
                    """
                    INSERT INTO midterm_memory (
                        user_id,
                        kind,
                        value,
                        source,
                        metadata_json,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        return len(rows)

    def list_recent(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    SELECT kind, value, source, metadata_json, created_at
                    FROM midterm_memory
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, safe_limit),
                )
                rows = cursor.fetchall()

        results: list[dict[str, Any]] = []
        for kind, value, source, metadata_json, created_at in rows:
            try:
                metadata = json.loads(metadata_json) if metadata_json else {}
            except json.JSONDecodeError:
                metadata = {}
            results.append(
                {
                    "kind": str(kind),
                    "value": str(value),
                    "source": str(source),
                    "metadata": metadata,
                    "created_at": str(created_at),
                }
            )
        return results
