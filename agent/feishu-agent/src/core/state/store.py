"""
状态存储抽象接口。

用于解耦内存实现与后续 Redis 实现。
"""

from __future__ import annotations

from typing import Protocol

from src.core.state.models import ConversationState


class StateStore(Protocol):
    """会话状态存储接口。"""

    def get(self, user_id: str) -> ConversationState | None:
        ...

    def set(self, user_id: str, state: ConversationState) -> None:
        ...

    def delete(self, user_id: str) -> None:
        ...

    def cleanup_expired(self) -> None:
        ...

    def active_count(self) -> int:
        ...
