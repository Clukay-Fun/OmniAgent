"""
会话状态模型定义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PendingDeleteState:
    record_id: str
    record_summary: str
    table_id: str | None
    created_at: float
    expires_at: float

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


@dataclass
class PaginationState:
    tool: str
    params: dict[str, Any]
    page_token: str | None
    current_page: int
    total: int | None
    created_at: float
    expires_at: float

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


@dataclass
class LastResultState:
    records: list[dict[str, Any]]
    record_ids: list[str]
    query_summary: str
    created_at: float
    expires_at: float

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


@dataclass
class ActiveRecordState:
    record_id: str
    record_summary: str
    table_id: str | None
    table_name: str | None
    record: dict[str, Any] = field(default_factory=dict)
    source: str = "unknown"
    created_at: float = 0.0
    expires_at: float = 0.0

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


@dataclass
class PendingActionState:
    action: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    expires_at: float = 0.0

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


@dataclass
class ConversationState:
    user_id: str
    created_at: float
    updated_at: float
    expires_at: float
    pending_delete: PendingDeleteState | None = None
    pagination: PaginationState | None = None
    last_result: LastResultState | None = None
    last_result_ids: list[str] = field(default_factory=list)
    active_table_id: str | None = None
    active_table_name: str | None = None
    active_record: ActiveRecordState | None = None
    pending_action: PendingActionState | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at
