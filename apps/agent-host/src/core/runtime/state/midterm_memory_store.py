"""
描述: 提供基于SQLite的轻量级中期记忆持久化功能。
主要功能:
    - 从请求和结果中构建低成本的摘要项。
    - 将摘要项持久化到SQLite数据库中以供将来检索。
"""

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
    """
    表示中期记忆项的数据类。

    属性:
        - kind: 记忆项的类型。
        - value: 记忆项的值。
        - source: 记忆项的来源。
        - metadata: 记忆项的元数据，默认为空字典。
    """
    kind: str
    value: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


class RuleSummaryExtractor:
    """
    从请求和结果中构建低成本的摘要项。

    功能:
        - 初始化时设置最大关键词数量。
        - 从用户文本中提取关键词。
        - 构建事件摘要项。
    """

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
        """
        初始化RuleSummaryExtractor。

        参数:
            - max_keywords: 提取的最大关键词数量，默认为5。
        """
        self._max_keywords = max(1, max_keywords)

    def build_items(
        self,
        user_text: str,
        skill_name: str,
        result_data: dict[str, Any] | None,
    ) -> list[MidtermMemoryItem]:
        """
        构建摘要项列表。

        参数:
            - user_text: 用户输入的文本。
            - skill_name: 技能名称。
            - result_data: 技能返回的结果数据。

        返回:
            - 摘要项列表。
        """
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
        """
        从用户文本中提取关键词并构建关键词摘要项。

        参数:
            - user_text: 用户输入的文本。

        返回:
            - 关键词摘要项列表。
        """
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
    """
    将摘要项持久化到SQLite数据库中以供将来检索。

    功能:
        - 初始化数据库连接和表结构。
        - 写入摘要项到数据库。
        - 从数据库中检索最近的摘要项。
    """

    def __init__(self, db_path: str = "workspace/memory/midterm_memory.sqlite3") -> None:
        """
        初始化SQLiteMidtermMemoryStore。

        参数:
            - db_path: SQLite数据库文件路径，默认为"workspace/memory/midterm_memory.sqlite3"。
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        """
        建立SQLite数据库连接。

        返回:
            - SQLite数据库连接对象。
        """
        return sqlite3.connect(str(self._db_path))

    def _ensure_schema(self) -> None:
        """
        确保数据库表结构存在。

        功能:
            - 创建midterm_memory表（如果不存在）。
            - 创建索引以提高查询效率。
        """
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
        """
        将摘要项写入数据库。

        参数:
            - user_id: 用户ID。
            - items: 摘要项列表。

        返回:
            - 写入的摘要项数量。
        """
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
        """
        检索最近的摘要项。

        参数:
            - user_id: 用户ID。
            - limit: 检索的数量限制，默认为20。

        返回:
            - 最近的摘要项列表。
        """
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
