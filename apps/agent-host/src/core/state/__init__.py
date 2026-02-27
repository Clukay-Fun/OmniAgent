"""
描述: 会话状态管理模块。
主要功能:
    - 提供会话状态管理的核心类和方法
    - 支持不同类型的会话状态存储（内存、Redis、SQLite）
    - 定义会话状态相关的数据模型
"""

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

# region 类和函数定义

class ConversationStateManager:
    """
    会话状态管理类

    功能:
        - 管理会话状态的创建、更新和检索
        - 支持不同类型的会话状态存储
    """

# endregion

# region 工厂函数

def create_state_store():
    """
    创建会话状态存储实例

    功能:
        - 根据配置创建相应的会话状态存储实例
        - 支持内存、Redis、SQLite等存储类型
    """

# endregion

# region 数据模型

class ActiveRecordState:
    """
    活跃记录状态模型

    功能:
        - 定义活跃记录状态的数据结构
        - 用于存储和管理活跃的会话状态
    """

class ConversationState:
    """
    会话状态模型

    功能:
        - 定义会话状态的数据结构
        - 用于存储和管理会话的基本信息
    """

class LastResultState:
    """
    最后结果状态模型

    功能:
        - 定义最后结果状态的数据结构
        - 用于存储和管理会话的最后结果
    """

class OperationEntry:
    """
    操作条目模型

    功能:
        - 定义操作条目的数据结构
        - 用于记录会话中的操作
    """

class OperationExecutionStatus:
    """
    操作执行状态模型

    功能:
        - 定义操作执行状态的数据结构
        - 用于记录操作的执行状态
    """

class PendingActionState:
    """
    待处理操作状态模型

    功能:
        - 定义待处理操作状态的数据结构
        - 用于管理待处理的操作
    """

class PendingActionStatus:
    """
    待处理操作状态枚举

    功能:
        - 定义待处理操作的状态枚举
        - 用于标识操作的不同状态
    """

class PaginationState:
    """
    分页状态模型

    功能:
        - 定义分页状态的数据结构
        - 用于管理会话中的分页信息
    """

class PendingDeleteState:
    """
    待删除状态模型

    功能:
        - 定义待删除状态的数据结构
        - 用于管理待删除的会话状态
    """

# endregion

# region 存储类

class MemoryStateStore:
    """
    内存状态存储类

    功能:
        - 提供基于内存的会话状态存储
        - 适用于开发和测试环境
    """

class RedisStateStore:
    """
    Redis状态存储类

    功能:
        - 提供基于Redis的会话状态存储
        - 适用于生产环境
    """

class SQLiteMidtermMemoryStore:
    """
    SQLite中期记忆存储类

    功能:
        - 提供基于SQLite的中期记忆存储
        - 用于存储和管理中期记忆项
    """

# endregion

# region 辅助类

class MidtermMemoryItem:
    """
    中期记忆项模型

    功能:
        - 定义中期记忆项的数据结构
        - 用于存储和管理中期记忆信息
    """

class RuleSummaryExtractor:
    """
    规则摘要提取器类

    功能:
        - 提供规则摘要的提取功能
        - 用于从会话中提取规则摘要信息
    """

# endregion
