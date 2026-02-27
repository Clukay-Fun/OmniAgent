"""
描述: 提供内存管理和快照功能的核心模块
主要功能:
    - 内存管理
    - 内存快照
"""

from src.core.memory.manager import MemoryManager, MemorySnapshot

__all__ = ["MemoryManager", "MemorySnapshot"]

# region 类定义
class MemoryManager:
    """
    内存管理的核心类

    功能:
        - 管理内存的分配和释放
        - 提供内存使用情况的监控
    """
    pass

class MemorySnapshot:
    """
    内存快照类，用于捕获内存状态

    功能:
        - 捕获当前内存使用情况
        - 提供内存使用情况的分析
    """
    pass
# endregion

# region 导出定义
__all__ = ["MemoryManager", "MemorySnapshot"]
# endregion
