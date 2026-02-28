"""
描述: 会话状态管理模块。
主要功能:
    - 提供会话状态管理的核心类和方法
    - 支持不同类型的会话状态存储（内存、Redis、SQLite）
    - 定义会话状态相关的数据模型
"""

from __future__ import annotations

from src.core.state.manager import ConversationStateManager
from src.core.state.factory import create_state_store
from src.core.state.memory_store import MemoryStateStore
from src.core.state.redis_store import RedisStateStore
from src.core.state.midterm_memory_store import (
    MidtermMemoryItem,
    RuleSummaryExtractor,
    SQLiteMidtermMemoryStore,
)
from src.core.state.models import (
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
]
