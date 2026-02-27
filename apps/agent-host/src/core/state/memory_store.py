"""
描述: 基于内存的状态存储实现。
主要功能:
    - 提供内存中的状态存储，支持按会话键或用户ID获取、设置和删除状态。
    - 支持状态的过期清理。
"""

from __future__ import annotations

import threading
import time

from src.core.state.models import ConversationState


class MemoryStateStore:
    """内存状态存储，支持 TTL 清理。

    功能:
        - 提供线程安全的会话状态存储。
        - 支持按会话键或用户ID获取、设置和删除状态。
        - 提供过期状态的清理功能。
    """

    def __init__(self) -> None:
        """初始化内存状态存储。

        功能:
            - 初始化一个线程锁用于同步访问。
            - 初始化一个字典用于存储会话状态。
        """
        self._lock = threading.Lock()
        self._states: dict[str, ConversationState] = {}

    def get(self, session_key: str | None = None, *, user_id: str | None = None) -> ConversationState | None:
        """获取指定会话键或用户ID的状态。

        功能:
            - 根据会话键或用户ID构建键值。
            - 从存储中获取对应的状态。
        """
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
        """设置指定会话键或用户ID的状态。

        功能:
            - 根据会话键或用户ID构建键值。
            - 将状态存储到内存中。
        """
        key = str(session_key or user_id or "").strip()
        if not key or state is None:
            return
        with self._lock:
            self._states[key] = state

    def delete(self, session_key: str | None = None, *, user_id: str | None = None) -> None:
        """删除指定会话键或用户ID的状态。

        功能:
            - 根据会话键或用户ID构建键值。
            - 从存储中删除对应的状态。
        """
        key = str(session_key or user_id or "").strip()
        if not key:
            return
        with self._lock:
            self._states.pop(key, None)

    def list_session_keys(self) -> list[str]:
        """列出所有会话键。

        功能:
            - 返回存储中所有会话键的列表。
        """
        with self._lock:
            return list(self._states.keys())

    def cleanup_expired(self) -> None:
        """清理过期的状态。

        功能:
            - 获取当前时间。
            - 遍历存储中的状态，删除过期的状态。
        """
        now = time.time()
        with self._lock:
            expired = [uid for uid, state in self._states.items() if state.is_expired(now)]
            for uid in expired:
                self._states.pop(uid, None)

    def active_count(self) -> int:
        """获取活动状态的数量。

        功能:
            - 返回存储中活动状态的数量。
        """
        with self._lock:
            return len(self._states)
