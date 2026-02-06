"""
会话状态管理器。

职责：
- 管理短生命周期对话状态（删除确认、分页、最近结果）
- 提供统一读写接口，便于后续替换为 Redis 实现
"""

from __future__ import annotations

import time
from typing import Any

from src.core.state.models import (
    ConversationState,
    LastResultState,
    PaginationState,
    PendingDeleteState,
)
from src.core.state.store import StateStore


class ConversationStateManager:
    """会话状态管理器（基于 StateStore）。"""

    def __init__(
        self,
        store: StateStore,
        default_ttl_seconds: int = 1800,
        pending_delete_ttl_seconds: int = 300,
        pagination_ttl_seconds: int = 600,
        last_result_ttl_seconds: int = 600,
    ) -> None:
        self._store = store
        self._default_ttl = default_ttl_seconds
        self._pending_delete_ttl = pending_delete_ttl_seconds
        self._pagination_ttl = pagination_ttl_seconds
        self._last_result_ttl = last_result_ttl_seconds

    def active_count(self) -> int:
        return self._store.active_count()

    def cleanup_expired(self) -> None:
        self._store.cleanup_expired()

    def get_state(self, user_id: str) -> ConversationState:
        now = time.time()
        state = self._store.get(user_id)
        if state is None or state.is_expired(now):
            state = ConversationState(
                user_id=user_id,
                created_at=now,
                updated_at=now,
                expires_at=now + self._default_ttl,
            )
            self._store.set(user_id, state)
            return state

        # 子状态过期清理
        if state.pending_delete and state.pending_delete.is_expired(now):
            state.pending_delete = None
        if state.pagination and state.pagination.is_expired(now):
            state.pagination = None
        if state.last_result and state.last_result.is_expired(now):
            state.last_result = None

        state.updated_at = now
        state.expires_at = max(state.expires_at, now + self._default_ttl)
        self._store.set(user_id, state)
        return state

    def clear_user(self, user_id: str) -> None:
        self._store.delete(user_id)

    def set_pending_delete(self, user_id: str, record_id: str, record_summary: str) -> None:
        now = time.time()
        state = self.get_state(user_id)
        state.pending_delete = PendingDeleteState(
            record_id=record_id,
            record_summary=record_summary,
            created_at=now,
            expires_at=now + self._pending_delete_ttl,
        )
        state.updated_at = now
        self._store.set(user_id, state)

    def get_pending_delete(self, user_id: str) -> PendingDeleteState | None:
        state = self.get_state(user_id)
        return state.pending_delete

    def clear_pending_delete(self, user_id: str) -> None:
        state = self.get_state(user_id)
        state.pending_delete = None
        state.updated_at = time.time()
        self._store.set(user_id, state)

    def set_last_result(self, user_id: str, records: list[dict[str, Any]], query_summary: str) -> None:
        now = time.time()
        state = self.get_state(user_id)
        record_ids: list[str] = []
        for item in records:
            rid = item.get("record_id")
            if isinstance(rid, str) and rid:
                record_ids.append(rid)
        state.last_result = LastResultState(
            records=records,
            record_ids=record_ids,
            query_summary=query_summary,
            created_at=now,
            expires_at=now + self._last_result_ttl,
        )
        state.updated_at = now
        self._store.set(user_id, state)

    def get_last_result(self, user_id: str) -> LastResultState | None:
        state = self.get_state(user_id)
        return state.last_result

    def set_pagination(
        self,
        user_id: str,
        tool: str,
        params: dict[str, Any],
        page_token: str | None,
        current_page: int,
        total: int | None,
    ) -> None:
        now = time.time()
        state = self.get_state(user_id)
        state.pagination = PaginationState(
            tool=tool,
            params=params,
            page_token=page_token,
            current_page=current_page,
            total=total,
            created_at=now,
            expires_at=now + self._pagination_ttl,
        )
        state.updated_at = now
        self._store.set(user_id, state)

    def get_pagination(self, user_id: str) -> PaginationState | None:
        state = self.get_state(user_id)
        return state.pagination

    def clear_pagination(self, user_id: str) -> None:
        state = self.get_state(user_id)
        state.pagination = None
        state.updated_at = time.time()
        self._store.set(user_id, state)
