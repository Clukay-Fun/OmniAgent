"""
描述: 内存会话管理 (Session Manager)
主要功能:
    - 维护用户会话上下文
    - 管理消息历史 (Context Window)
    - 自动清理过期会话
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config import SessionSettings


@dataclass
class Session:
    """会话数据结构"""
    user_id: str
    messages: list[dict[str, str]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# region 会话管理器
class SessionManager:
    """
    内存会话管理器

    功能:
        - 会话生命周期管理 (创建、访问、过期清理)
        - 消息上下文维护 (History Window)
    """

    def __init__(self, settings: SessionSettings) -> None:
        """
        初始化管理器

        参数:
            settings: 会话配置 (TTL, Max Rounds)
        """
        self._settings = settings
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, user_id: str) -> Session:
        """获取或创建会话"""
        session = self._sessions.get(user_id)
        if not session:
            session = Session(user_id=user_id)
            self._sessions[user_id] = session
        session.last_active = datetime.now(timezone.utc)
        return session

    def add_message(self, user_id: str, role: str, content: str) -> None:
        """
        追加会话消息

        参数:
            user_id: 用户 ID
            role: 角色 (user/assistant)
            content: 消息内容
        """
        session = self.get_or_create(user_id)
        session.messages.append({"role": role, "content": content})
        session.last_active = datetime.now(timezone.utc)
        if len(session.messages) > self._settings.max_rounds * 2:
            session.messages = session.messages[-self._settings.max_rounds * 2 :]

    def get_context(self, user_id: str) -> list[dict[str, str]]:
        """获取当前上下文消息列表"""
        session = self.get_or_create(user_id)
        return list(session.messages)

    def cleanup_expired(self) -> None:
        """清理过期会话"""
        ttl = timedelta(minutes=self._settings.ttl_minutes)
        now = datetime.now(timezone.utc)
        expired = [
            user_id
            for user_id, session in self._sessions.items()
            if now - session.last_active > ttl
        ]
        for user_id in expired:
            self._sessions.pop(user_id, None)
# endregion
