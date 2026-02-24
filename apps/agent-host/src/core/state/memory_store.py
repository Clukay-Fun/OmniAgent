"""
基于内存的状态存储实现。
"""

from __future__ import annotations

import threading
import time

from src.core.state.models import ConversationState


class MemoryStateStore:
    """内存状态存储，支持 TTL 清理。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, ConversationState] = {}

    def get(self, session_key: str | None = None, *, user_id: str | None = None) -> ConversationState | None:
        key = str(session_key or user_id or "").strip()
        if not key:
            return None
        with self._lock:
            return self._states.get(key)

    def set(
        self,
        session_key: str | None = None,
        state: ConversationState | None = None,
        *,
        user_id: str | None = None,
    ) -> None:
        key = str(session_key or user_id or "").strip()
        if not key or state is None:
            return
        with self._lock:
            self._states[key] = state

    def delete(self, session_key: str | None = None, *, user_id: str | None = None) -> None:
        key = str(session_key or user_id or "").strip()
        if not key:
            return
        with self._lock:
            self._states.pop(key, None)

    def list_session_keys(self) -> list[str]:
        with self._lock:
            return list(self._states.keys())

    def cleanup_expired(self) -> None:
        now = time.time()
        with self._lock:
            expired = [uid for uid, state in self._states.items() if state.is_expired(now)]
            for uid in expired:
                self._states.pop(uid, None)

    def active_count(self) -> int:
        with self._lock:
            return len(self._states)
