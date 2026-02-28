"""
描述: 提供会话状态存储的抽象接口，用于解耦内存实现与后续 Redis 实现。
主要功能:
    - 定义会话状态存储的基本操作接口
"""

from __future__ import annotations

from typing import Protocol

from src.core.runtime.state.models import ConversationState

# region 会话状态存储接口定义
class StateStore(Protocol):
    """
    会话状态存储接口。

    功能:
        - 提供获取会话状态的方法
        - 提供设置会话状态的方法
        - 提供删除会话状态的方法
        - 提供列出所有会话键的方法
        - 提供清理过期会话的方法
        - 提供获取活跃会话数量的方法
    """

    def get(self, session_key: str) -> ConversationState | None:
        """
        获取指定会话键的会话状态。

        功能:
            - 根据会话键返回对应的会话状态
            - 如果会话键不存在，返回 None
        """
        ...

    def set(self, session_key: str, state: ConversationState) -> None:
        """
        设置指定会话键的会话状态。

        功能:
            - 将会话状态与会话键关联存储
        """
        ...

    def delete(self, session_key: str) -> None:
        """
        删除指定会话键的会话状态。

        功能:
            - 根据会话键删除对应的会话状态
        """
        ...

    def list_session_keys(self) -> list[str]:
        """
        列出所有会话键。

        功能:
            - 返回所有存储的会话键列表
        """
        ...

    def cleanup_expired(self) -> None:
        """
        清理过期的会话状态。

        功能:
            - 删除所有过期的会话状态
        """
        ...

    def active_count(self) -> int:
        """
        获取活跃会话的数量。

        功能:
            - 返回当前活跃的会话数量
        """
        ...
# endregion
