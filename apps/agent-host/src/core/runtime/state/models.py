"""
描述: 定义会话状态模型。
主要功能:
    - 定义各种会话状态的数据类
    - 提供状态检查和转换的方法
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from enum import Enum


@dataclass
class PendingDeleteState:
    """
    待删除记录的状态。

    功能:
        - 存储待删除记录的ID、摘要、表ID、创建时间和过期时间
        - 提供检查记录是否过期的方法
    """
    record_id: str
    record_summary: str
    table_id: str | None
    created_at: float
    expires_at: float

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


@dataclass
class PaginationState:
    """
    分页状态。

    功能:
        - 存储分页工具、参数、页码、总记录数、创建时间和过期时间
        - 提供检查分页状态是否过期的方法
    """
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
    """
    最后一次查询结果的状态。

    功能:
        - 存储查询结果记录、记录ID、查询摘要、创建时间和过期时间
        - 提供检查结果是否过期的方法
    """
    records: list[dict[str, Any]]
    record_ids: list[str]
    query_summary: str
    created_at: float
    expires_at: float

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at


@dataclass
class ActiveRecordState:
    """
    活跃记录的状态。

    功能:
        - 存储记录ID、摘要、表ID、表名、记录内容、来源、创建时间和过期时间
        - 提供检查记录是否过期的方法
    """
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
    """
    消息分块的状态。

    功能:
        - 存储消息分段、开始时间和最后更新时间
    """
    segments: list[str] = field(default_factory=list)
    started_at: float = 0.0
    last_at: float = 0.0


class PendingActionStatus(str, Enum):
    """
    待处理操作的状态枚举。
    """
    COLLECTING = "collecting"
    CONFIRMABLE = "confirmable"
    EXECUTED = "executed"
    INVALIDATED = "invalidated"


class OperationExecutionStatus(str, Enum):
    """
    操作执行状态枚举。
    """
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class OperationEntry:
    """
    每个操作的执行状态，用于批量待处理操作。

    功能:
        - 存储操作索引、负载、状态、错误代码、错误详情和执行时间
        - 提供初始化后处理的方法
    """
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
    """
    待处理操作的状态。

    功能:
        - 存储操作、负载、操作条目列表、状态、创建时间和过期时间
        - 提供初始化后处理的方法
        - 提供迭代操作负载的方法
        - 提供检查状态是否过期的方法
        - 提供状态转换的方法
    """
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
        """
        转换状态。在无效转换时抛出 ValueError。

        功能:
            - 检查当前状态是否过期并进行相应的状态转换
            - 检查目标状态是否为有效转换
            - 更新状态
        """
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
    """
    对话状态。

    功能:
        - 存储用户ID、创建时间、更新时间、过期时间、待删除状态、分页状态、最后一个结果状态、最后一个结果ID列表、活跃表ID、活跃表名、活跃记录、待处理操作状态、消息分块状态和额外信息
        - 提供初始化后处理的方法
        - 提供会话键的属性
        - 提供检查状态是否过期的方法
        - 提供从字典创建对话状态的方法
    """
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
        """
        从字典创建对话状态。

        功能:
            - 从字典中提取数据并创建对话状态实例
            - 处理待处理操作和消息分块的状态
        """
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
