"""
描述: 提供内存管理和快照功能的核心模块
主要功能:
    - 内存管理
    - 内存快照
"""

from __future__ import annotations

from src.core.runtime.memory.manager import MemoryManager, MemorySnapshot

__all__ = ["MemoryManager", "MemorySnapshot"]
