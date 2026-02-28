"""
描述: 提供处理状态的抽象类和枚举
主要功能:
    - 定义处理状态的枚举类型
    - 定义处理状态事件的数据类
    - 定义处理状态事件发射器的协议
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Protocol

# region 枚举定义
class ProcessingStatus(str, Enum):
    """
    定义处理状态的枚举类型

    功能:
        - 提供三种处理状态: THINKING, SEARCHING, DONE
    """
    THINKING = "thinking"
    SEARCHING = "searching"
    DONE = "done"
# endregion

# region 数据类定义
@dataclass
class ProcessingStatusEvent:
    """
    定义处理状态事件的数据类

    功能:
        - 存储处理状态事件的相关信息
        - 包括状态、用户ID、聊天ID、聊天类型、消息ID和元数据
    """
    status: ProcessingStatus
    user_id: str
    chat_id: str | None = None
    chat_type: str | None = None
    message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
# endregion

# region 协议定义
class ProcessingStatusEmitter(Protocol):
    """
    定义处理状态事件发射器的协议

    功能:
        - 定义一个可调用对象，用于发射处理状态事件
    """
    def __call__(self, event: ProcessingStatusEvent) -> Awaitable[None] | None: ...
# endregion
