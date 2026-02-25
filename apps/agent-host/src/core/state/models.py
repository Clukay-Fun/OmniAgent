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
class MessageChunkState:
    segments: list[str] = field(default_factory=list)
    started_at: float = 0.0
    last_at: float = 0.0


from enum import Enum


class PendingActionStatus(str, Enum):
    COLLECTING = "collecting"
    CONFIRMABLE = "confirmable"
    EXECUTED = "executed"
    INVALIDATED = "invalidated"


class OperationExecutionStatus(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class OperationEntry:
    """Per-operation execution state for batch pending actions."""

    index: int
    payload: dict[str, Any] = field(default_factory=dict)
    status: OperationExecutionStatus = OperationExecutionStatus.PENDING
    error_code: str | None = None
    error_detail: str | None = None
    executed_at: float | None = None

    def __post_init__(self) -> None:
        try:
            self.index = int(self.index)
        except Exception:
            self.index = 0

        if not isinstance(self.payload, dict):
            self.payload = {}

        if not isinstance(self.status, OperationExecutionStatus):
            try:
                self.status = OperationExecutionStatus(str(self.status))
            except ValueError:
                self.status = OperationExecutionStatus.PENDING

        if self.error_code is not None:
            error_code = str(self.error_code).strip()
            self.error_code = error_code or None

        if self.error_detail is not None:
            error_detail = str(self.error_detail).strip()
            self.error_detail = error_detail or None

        if self.executed_at is not None:
            try:
                self.executed_at = float(self.executed_at)
            except Exception:
                self.executed_at = None


_LEGACY_PENDING_STATUS_MAP: dict[str, PendingActionStatus] = {
    "pending": PendingActionStatus.CONFIRMABLE,
    "confirmed": PendingActionStatus.EXECUTED,
    "cancelled": PendingActionStatus.INVALIDATED,
    "expired": PendingActionStatus.INVALIDATED,
}


_VALID_TRANSITIONS: dict[PendingActionStatus, set[PendingActionStatus]] = {
    PendingActionStatus.COLLECTING: {
        PendingActionStatus.COLLECTING,
        PendingActionStatus.CONFIRMABLE,
        PendingActionStatus.INVALIDATED,
    },
    PendingActionStatus.CONFIRMABLE: {
        PendingActionStatus.EXECUTED,
        PendingActionStatus.INVALIDATED,
    },
    PendingActionStatus.EXECUTED: set(),
    PendingActionStatus.INVALIDATED: set(),
}


@dataclass
class PendingActionState:
    action: str
    payload: dict[str, Any] = field(default_factory=dict)
    operations: list[OperationEntry] = field(default_factory=list)
    status: PendingActionStatus = PendingActionStatus.CONFIRMABLE
    created_at: float = 0.0
    expires_at: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.payload, dict):
            self.payload = {}

        normalized_operations: list[OperationEntry] = []
        raw_operations = self.operations
        if not isinstance(raw_operations, list):
            raw_operations = []
        for index, item in enumerate(raw_operations):
            normalized = self._normalize_operation_item(item, index)
            if normalized is not None:
                normalized_operations.append(normalized)
        self.operations = normalized_operations

        if not self.operations:
            payload_operations = self.payload.get("operations") if isinstance(self.payload, dict) else None
            if isinstance(payload_operations, list):
                for index, item in enumerate(payload_operations):
                    normalized = self._normalize_operation_item(item, index)
                    if normalized is not None:
                        self.operations.append(normalized)

        if isinstance(self.status, PendingActionStatus):
            return
        status_text = str(self.status)
        if status_text in _LEGACY_PENDING_STATUS_MAP:
            self.status = _LEGACY_PENDING_STATUS_MAP[status_text]
            return
        try:
            self.status = PendingActionStatus(status_text)
        except ValueError:
            self.status = PendingActionStatus.CONFIRMABLE

    def iter_operation_payloads(self) -> list[dict[str, Any]]:
        if self.operations:
            return [
                dict(item.payload)
                for item in self.operations
                if isinstance(item, OperationEntry) and item.status == OperationExecutionStatus.PENDING
            ]
        if self.payload:
            return [dict(self.payload)]
        return []

    @staticmethod
    def _normalize_operation_item(item: Any, index: int) -> OperationEntry | None:
        if isinstance(item, OperationEntry):
            return item

        if not isinstance(item, dict):
            return None

        if isinstance(item.get("payload"), dict):
            payload = dict(item.get("payload") or {})
            return OperationEntry(
                index=item.get("index", index),
                payload=payload,
                status=item.get("status", OperationExecutionStatus.PENDING.value),
                error_code=item.get("error_code"),
                error_detail=item.get("error_detail"),
                executed_at=item.get("executed_at"),
            )

        # Legacy shape: operations is a plain payload dict
        return OperationEntry(index=index, payload=dict(item))

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at

    def transition_to(self, target: PendingActionStatus, now: float | None = None) -> None:
        """Transition status. Raises ValueError on invalid transition."""
        import time as _time
        _now = now if now is not None else _time.time()
        if self.is_expired(_now) and self.status in {
            PendingActionStatus.COLLECTING,
            PendingActionStatus.CONFIRMABLE,
        }:
            self.status = PendingActionStatus.INVALIDATED
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
    message_chunk: MessageChunkState | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.pending_action, dict):
            try:
                self.pending_action = PendingActionState(**self.pending_action)
            except Exception:
                self.pending_action = None

        if isinstance(self.message_chunk, dict):
            try:
                self.message_chunk = MessageChunkState(**self.message_chunk)
            except Exception:
                self.message_chunk = None

    @property
    def session_key(self) -> str:
        return self.user_id

    @session_key.setter
    def session_key(self, value: str) -> None:
        self.user_id = str(value)

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversationState:
        payload = dict(data)
        chunk_data = payload.pop("message_chunk", None)
        pending_action_data = payload.get("pending_action")
        if isinstance(pending_action_data, dict):
            try:
                payload["pending_action"] = PendingActionState(**pending_action_data)
            except Exception:
                payload["pending_action"] = None
        state = cls(**payload)
        if isinstance(chunk_data, dict):
            state.message_chunk = MessageChunkState(**chunk_data)
        return state
