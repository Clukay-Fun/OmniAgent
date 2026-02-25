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


from enum import Enum


class PendingActionStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


_VALID_TRANSITIONS: dict[PendingActionStatus, set[PendingActionStatus]] = {
    PendingActionStatus.PENDING: {
        PendingActionStatus.CONFIRMED,
        PendingActionStatus.CANCELLED,
        PendingActionStatus.EXPIRED,
    },
    PendingActionStatus.CONFIRMED: set(),
    PendingActionStatus.CANCELLED: set(),
    PendingActionStatus.EXPIRED: set(),
}


@dataclass
class PendingActionState:
    action: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: PendingActionStatus = PendingActionStatus.PENDING
    created_at: float = 0.0
    expires_at: float = 0.0

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at

    def transition_to(self, target: PendingActionStatus, now: float | None = None) -> None:
        """Transition status. Raises ValueError on invalid transition."""
        import time as _time
        _now = now if now is not None else _time.time()
        if self.is_expired(_now) and self.status == PendingActionStatus.PENDING:
            self.status = PendingActionStatus.EXPIRED
        if target not in _VALID_TRANSITIONS.get(self.status, set()):
            raise ValueError(
                f"invalid pending_action transition: {self.status.value} -> {target.value}"
            )
        self.status = target


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

    @property
    def session_key(self) -> str:
        return self.user_id

    @session_key.setter
    def session_key(self, value: str) -> None:
        self.user_id = str(value)

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at
