"""
描述: 会话状态管理模块。
主要功能:
    - 提供会话状态管理的核心类和方法
    - 支持不同类型的会话状态存储（内存、Redis、SQLite）
    - 定义会话状态相关的数据模型
"""

from __future__ import annotations

from src.core.runtime.state.manager import ConversationStateManager
from src.core.runtime.state.factory import create_state_store
from src.core.runtime.state.memory_store import MemoryStateStore
from src.core.runtime.state.redis_store import RedisStateStore
from src.core.runtime.state.midterm_memory_store import (
    MidtermMemoryItem,
    RuleSummaryExtractor,
    SQLiteMidtermMemoryStore,
)
from src.core.runtime.state.models import (
    ActiveRecordState,
    ConversationState,
    LastResultState,
    OperationEntry,
    OperationExecutionStatus,
    PendingActionState,
    PendingActionStatus,
    PaginationState,
    PendingDeleteState,
)
from src.core.runtime.state.session import Session, SessionManager

__all__ = [
    "ConversationStateManager",
    "create_state_store",
    "MemoryStateStore",
    "RedisStateStore",
    "SQLiteMidtermMemoryStore",
    "RuleSummaryExtractor",
    "MidtermMemoryItem",
    "ConversationState",
    "ActiveRecordState",
    "OperationEntry",
    "OperationExecutionStatus",
    "PendingActionState",
    "PendingActionStatus",
    "PendingDeleteState",
    "PaginationState",
    "LastResultState",
    "Session",
    "SessionManager",
]
