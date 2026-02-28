"""
描述: 内存会话管理 (Session Manager)
主要功能:
    - 维护用户会话上下文
    - 管理消息历史 (Context Window)
    - 自动清理过期会话
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from src.config import SessionSettings


logger = logging.getLogger(__name__)


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
        self._expire_listeners: list[Callable[[str], None]] = []

    def register_expire_listener(self, listener: Callable[[str], None]) -> None:
        """注册会话过期回调监听器。"""
        self._expire_listeners.append(listener)

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

    def trim_context_to_token_budget(
        self,
        user_id: str,
        max_tokens: int,
        keep_recent_messages: int = 2,
    ) -> int:
        """Trim oldest messages when estimated tokens exceed budget."""
        session = self.get_or_create(user_id)
        keep_recent_messages = max(0, keep_recent_messages)
        max_tokens = max(1, max_tokens)

        removed = 0
        while (
            len(session.messages) > keep_recent_messages
            and self._estimate_messages_tokens(session.messages) > max_tokens
        ):
            session.messages.pop(0)
            removed += 1

        if removed > 0:
            logger.warning(
                "会话上下文超过 token 预算，已裁剪最旧消息",
                extra={
                    "event_code": "session.context.trimmed",
                    "user_id": user_id,
                    "removed_messages": removed,
                    "remaining_messages": len(session.messages),
                    "max_tokens": max_tokens,
                },
            )
        return removed

    def _estimate_messages_tokens(self, messages: list[dict[str, str]]) -> int:
        return sum(self._estimate_message_tokens(message) for message in messages)

    def _estimate_message_tokens(self, message: dict[str, str]) -> int:
        role = str(message.get("role", "")).strip()
        content = str(message.get("content", "")).strip()
        role_tokens = math.ceil(len(role) / 4) if role else 1
        content_tokens = math.ceil(len(content) / 4) if content else 1
        return role_tokens + content_tokens + 4

    def get_context(self, user_id: str) -> list[dict[str, str]]:
        """获取当前上下文消息列表"""
        session = self.get_or_create(user_id)
        return list(session.messages)

    def clear_user(self, user_id: str) -> None:
        """清除指定用户会话。"""
        self._sessions.pop(user_id, None)

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
            for listener in self._expire_listeners:
                try:
                    listener(user_id)
                except Exception:
                    continue
# endregion
