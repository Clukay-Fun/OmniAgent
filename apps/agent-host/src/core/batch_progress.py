"""
描述: 提供批量处理进度事件的抽象类和协议。
主要功能:
    - 定义批量处理的不同阶段。
    - 定义批量处理事件的数据结构。
    - 定义批量处理事件发射器的协议。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Protocol

# region 枚举定义
class BatchProgressPhase(str, Enum):
    """
    批量处理的不同阶段。

    功能:
        - 定义批量处理的开始阶段。
        - 定义批量处理的完成阶段。
    """
    START = "start"
    COMPLETE = "complete"
# endregion

# region 数据类定义
@dataclass
class BatchProgressEvent:
    """
    批量处理事件的数据结构。

    功能:
        - 包含事件的阶段。
        - 包含用户ID。
        - 包含总任务数。
        - 包含成功任务数。
        - 包含失败任务数。
        - 包含额外的元数据。
    """
    phase: BatchProgressPhase
    user_id: str
    total: int
    succeeded: int = 0
    failed: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
# endregion

# region 协议定义
class BatchProgressEmitter(Protocol):
    """
    批量处理事件发射器的协议。

    功能:
        - 定义一个可调用对象，用于发射批量处理事件。
    """
    def __call__(self, event: BatchProgressEvent) -> Awaitable[None] | None: ...
# endregion
