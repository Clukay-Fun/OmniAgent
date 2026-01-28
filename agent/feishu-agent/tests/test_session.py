from __future__ import annotations

from datetime import datetime, timedelta

from src.agent.session import SessionManager
from src.config import SessionSettings


def test_session_truncate_and_cleanup() -> None:
    settings = SessionSettings(max_rounds=2, ttl_minutes=1)
    manager = SessionManager(settings)

    manager.add_message("u1", "user", "m1")
    manager.add_message("u1", "assistant", "m2")
    manager.add_message("u1", "user", "m3")
    manager.add_message("u1", "assistant", "m4")
    manager.add_message("u1", "user", "m5")

    context = manager.get_context("u1")
    assert len(context) == 4

    session = manager.get_or_create("u1")
    session.last_active = datetime.utcnow() - timedelta(minutes=10)
    manager.cleanup_expired()
    assert manager.get_context("u1")
