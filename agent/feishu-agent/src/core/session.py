"""
In-memory session manager.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config import SessionSettings


@dataclass
class Session:
    user_id: str
    messages: list[dict[str, str]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SessionManager:
    def __init__(self, settings: SessionSettings) -> None:
        self._settings = settings
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, user_id: str) -> Session:
        session = self._sessions.get(user_id)
        if not session:
            session = Session(user_id=user_id)
            self._sessions[user_id] = session
        session.last_active = datetime.now(timezone.utc)
        return session

    def add_message(self, user_id: str, role: str, content: str) -> None:
        session = self.get_or_create(user_id)
        session.messages.append({"role": role, "content": content})
        session.last_active = datetime.now(timezone.utc)
        if len(session.messages) > self._settings.max_rounds * 2:
            session.messages = session.messages[-self._settings.max_rounds * 2 :]

    def get_context(self, user_id: str) -> list[dict[str, str]]:
        session = self.get_or_create(user_id)
        return list(session.messages)

    def cleanup_expired(self) -> None:
        ttl = timedelta(minutes=self._settings.ttl_minutes)
        now = datetime.now(timezone.utc)
        expired = [
            user_id
            for user_id, session in self._sessions.items()
            if now - session.last_active > ttl
        ]
        for user_id in expired:
            self._sessions.pop(user_id, None)
