"""
会话状态管理器。

职责：
- 管理短生命周期对话状态（删除确认、分页、最近结果）
- 提供统一读写接口，便于后续替换为 Redis 实现
"""

from __future__ import annotations

import time
from typing import Any, cast

from src.core.state.models import (
    ActiveRecordState,
    ConversationState,
    LastResultState,
    MessageChunkState,
    OperationEntry,
    PendingActionState,
    PendingActionStatus,
    PaginationState,
    PendingDeleteState,
)
from src.core.state.store import StateStore


class ConversationStateManager:
    """会话状态管理器（基于 StateStore）。"""

    CHUNK_STALE_SECONDS = 9.0

    @staticmethod
    def _resolve_pending_action_status(action: str, payload: dict[str, Any]) -> PendingActionStatus:
        explicit = str(payload.get("status") or "").strip().lower()
        if explicit:
            try:
                return PendingActionStatus(explicit)
            except ValueError:
                pass

        if action == "update_collect_fields":
            return PendingActionStatus.COLLECTING

        awaiting_confirm = bool(payload.get("awaiting_confirm"))
        missing_fields = payload.get("missing_fields")
        if isinstance(missing_fields, list) and missing_fields and not awaiting_confirm:
            return PendingActionStatus.COLLECTING

        if awaiting_confirm:
            return PendingActionStatus.CONFIRMABLE

        if action.startswith("batch_") or action in {
            "create_record",
            "update_record",
            "close_record",
            "delete_record",
            "create_reminder",
            "query_list_navigation",
        }:
            return PendingActionStatus.CONFIRMABLE

        return PendingActionStatus.COLLECTING

    def __init__(
        self,
        store: StateStore,
        default_ttl_seconds: int = 1800,
        pending_delete_ttl_seconds: int = 300,
        pagination_ttl_seconds: int = 600,
        last_result_ttl_seconds: int = 600,
        active_record_ttl_seconds: int = 1800,
        pending_action_ttl_seconds: int = 300,
    ) -> None:
        self._store = store
        self._default_ttl = default_ttl_seconds
        self._pending_delete_ttl = pending_delete_ttl_seconds
        self._pagination_ttl = pagination_ttl_seconds
        self._last_result_ttl = last_result_ttl_seconds
        self._active_record_ttl = active_record_ttl_seconds
        self._pending_action_ttl = pending_action_ttl_seconds

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
            state.last_result_ids = []
        if state.active_record and state.active_record.is_expired(now):
            state.active_record = None
        if state.pending_action and state.pending_action.is_expired(now):
            # S2: 用状态机迁移而非直接清空，保留 INVALIDATED 记录
            if state.pending_action.status in {
                PendingActionStatus.COLLECTING,
                PendingActionStatus.CONFIRMABLE,
            }:
                try:
                    state.pending_action.transition_to(PendingActionStatus.INVALIDATED, now=now)
                except ValueError:
                    pass
            pending_history = state.extras.get("pending_action_history")
            if not isinstance(pending_history, list):
                pending_history = []
            pending_history.append(
                {
                    "action": state.pending_action.action,
                    "status": str(getattr(state.pending_action.status, "value", state.pending_action.status)),
                    "at": now,
                }
            )
            state.extras["pending_action_history"] = pending_history[-20:]
            state.pending_action = None

        state.updated_at = now
        state.expires_at = max(state.expires_at, now + self._default_ttl)
        self._store.set(user_id, state)
        return state

    def get_state_by_session_key(self, session_key: str) -> ConversationState:
        """session_key 语义入口（兼容旧 get_state）。"""
        return self.get_state(session_key)

    def clear_user(self, user_id: str) -> None:
        self._store.delete(user_id)

    def clear_session(self, session_key: str) -> None:
        """session_key 语义入口（兼容旧 clear_user）。"""
        self.clear_user(session_key)

    def set_pending_delete(
        self,
        user_id: str,
        record_id: str,
        record_summary: str,
        table_id: str | None = None,
    ) -> None:
        now = time.time()
        state = self.get_state(user_id)
        state.pending_delete = PendingDeleteState(
            record_id=record_id,
            record_summary=record_summary,
            table_id=table_id,
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
        state.last_result_ids = record_ids
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

    def get_last_result_payload(self, user_id: str) -> dict[str, Any] | None:
        last_result = self.get_last_result(user_id)
        if not last_result:
            return None
        return {
            "records": last_result.records,
            "record_ids": last_result.record_ids,
            "query_summary": last_result.query_summary,
        }

    def set_last_skill(self, user_id: str, skill_name: str) -> None:
        state = self.get_state(user_id)
        state.extras["last_skill"] = skill_name
        state.updated_at = time.time()
        self._store.set(user_id, state)

    def get_last_skill(self, user_id: str) -> str | None:
        state = self.get_state(user_id)
        value = state.extras.get("last_skill")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def set_reply_preferences(self, user_id: str, preferences: dict[str, str]) -> None:
        state = self.get_state(user_id)
        current = state.extras.get("reply_preferences")
        merged = dict(current) if isinstance(current, dict) else {}
        for key in ("tone", "length"):
            value = preferences.get(key)
            if isinstance(value, str) and value.strip():
                merged[key] = value.strip().lower()
        if merged:
            state.extras["reply_preferences"] = merged
            state.updated_at = time.time()
            self._store.set(user_id, state)

    def get_reply_preferences(self, user_id: str) -> dict[str, str]:
        state = self.get_state(user_id)
        raw = state.extras.get("reply_preferences")
        if not isinstance(raw, dict):
            return {}
        result: dict[str, str] = {}
        for key in ("tone", "length"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                result[key] = value.strip().lower()
        return result

    def set_active_table(self, user_id: str, table_id: str | None, table_name: str | None = None) -> None:
        state = self.get_state(user_id)
        state.active_table_id = str(table_id).strip() if table_id else None
        state.active_table_name = str(table_name).strip() if table_name else None
        state.updated_at = time.time()
        self._store.set(user_id, state)

    def get_active_table(self, user_id: str) -> dict[str, str | None]:
        state = self.get_state(user_id)
        return {
            "table_id": state.active_table_id,
            "table_name": state.active_table_name,
        }

    def clear_active_table(self, user_id: str) -> None:
        state = self.get_state(user_id)
        state.active_table_id = None
        state.active_table_name = None
        state.updated_at = time.time()
        self._store.set(user_id, state)

    def set_active_record(
        self,
        user_id: str,
        record: dict[str, Any],
        table_id: str | None = None,
        table_name: str | None = None,
        source: str = "unknown",
    ) -> None:
        now = time.time()
        state = self.get_state(user_id)
        record_id = str(record.get("record_id") or "").strip()
        if not record_id:
            return

        if table_id is None:
            table_id = str(record.get("table_id") or "").strip() or None
        if table_name is None:
            table_name = str(record.get("table_name") or "").strip() or None

        fields = record.get("fields_text") or record.get("fields") or {}
        summary = ""
        if isinstance(fields, dict):
            summary = str(fields.get("案号") or fields.get("项目ID") or "").strip()

        state.active_record = ActiveRecordState(
            record_id=record_id,
            record_summary=summary,
            table_id=table_id,
            table_name=table_name,
            record=record,
            source=source,
            created_at=now,
            expires_at=now + self._active_record_ttl,
        )
        if table_id:
            state.active_table_id = table_id
        if table_name:
            state.active_table_name = table_name
        state.updated_at = now
        self._store.set(user_id, state)

    def clear_active_record(self, user_id: str) -> None:
        state = self.get_state(user_id)
        state.active_record = None
        state.updated_at = time.time()
        self._store.set(user_id, state)

    def get_active_record(self, user_id: str) -> ActiveRecordState | None:
        state = self.get_state(user_id)
        return state.active_record

    def get_message_chunk(
        self,
        user_id: str,
        now: float | None = None,
        enforce_stale: bool = True,
    ) -> MessageChunkState | None:
        state = self.get_state(user_id)
        chunk = state.message_chunk
        if not enforce_stale:
            return chunk
        current = float(now) if now is not None else time.time()
        if chunk and (current - chunk.last_at > self.CHUNK_STALE_SECONDS):
            state.message_chunk = None
            state.updated_at = current
            self._store.set(user_id, state)
            return None
        return chunk

    def set_message_chunk(self, user_id: str, chunk: MessageChunkState | None) -> None:
        state = self.get_state(user_id)
        state.message_chunk = chunk
        state.updated_at = time.time()
        self._store.set(user_id, state)

    def set_pending_action(
        self,
        user_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
        operations: list[dict[str, Any] | OperationEntry] | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        now = time.time()
        state = self.get_state(user_id)
        normalized_operations: list[OperationEntry] = []
        if isinstance(operations, list):
            for index, item in enumerate(operations):
                if isinstance(item, OperationEntry):
                    normalized_operations.append(item)
                elif isinstance(item, dict):
                    normalized_operations.append(OperationEntry(index=index, payload=dict(item)))
        state.pending_action = PendingActionState(
            action=str(action).strip(),
            payload=payload or {},
            operations=cast(list[OperationEntry], normalized_operations),
            status=self._resolve_pending_action_status(str(action).strip(), payload or {}),
            created_at=now,
            expires_at=now + (ttl_seconds if ttl_seconds is not None else self._pending_action_ttl),
        )
        state.updated_at = now
        self._store.set(user_id, state)

    def update_pending_action_operations(self, user_id: str, pending: PendingActionState) -> PendingActionState | None:
        """Persist per-operation status updates for the current pending action."""
        state = self.get_state(user_id)
        current = state.pending_action
        if current is None:
            return None

        if str(current.action or "").strip() != str(pending.action or "").strip():
            return None

        current.operations = list(pending.operations)
        current.payload = dict(pending.payload) if isinstance(pending.payload, dict) else {}
        current.status = pending.status
        state.updated_at = time.time()
        self._store.set(user_id, state)
        return current

    def get_pending_action(self, user_id: str) -> PendingActionState | None:
        state = self.get_state(user_id)
        return state.pending_action

    def clear_pending_action(self, user_id: str) -> None:
        state = self.get_state(user_id)
        state.pending_action = None
        state.updated_at = time.time()
        self._store.set(user_id, state)

    def confirm_pending_action(self, user_id: str) -> PendingActionState | None:
        """S2: 确认 pending_action，用状态机迁移。返回迁移后的 state 或 None。"""
        state = self.get_state(user_id)
        pa = state.pending_action
        if pa is None:
            return None
        now = time.time()
        try:
            pa.transition_to(PendingActionStatus.EXECUTED, now=now)
        except ValueError:
            return None
        state.updated_at = now
        self._store.set(user_id, state)
        return pa

    def cancel_pending_action(self, user_id: str) -> PendingActionState | None:
        """S2: 取消 pending_action，用状态机迁移。返回迁移后的 state 或 None。"""
        state = self.get_state(user_id)
        pa = state.pending_action
        if pa is None:
            return None
        now = time.time()
        try:
            pa.transition_to(PendingActionStatus.INVALIDATED, now=now)
        except ValueError:
            return None
        state.updated_at = now
        self._store.set(user_id, state)
        return pa
