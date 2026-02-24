from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.skills.reminder import ReminderSkill
from src.core.types import SkillContext


class _FakeDB:
    def __init__(self) -> None:
        self.saved: list[dict[str, str]] = []

    async def create_reminder(
        self,
        *,
        user_id: str,
        chat_id: str | None,
        content: str,
        due_at: datetime,
        priority: str,
        status: str,
        source: str,
    ) -> int:
        self.saved.append(
            {
                "user_id": user_id,
                "chat_id": str(chat_id or ""),
                "content": content,
                "due_at": due_at.strftime("%Y-%m-%d %H:%M"),
                "priority": priority,
                "status": status,
                "source": source,
            }
        )
        return len(self.saved)


def test_reminder_skill_executes_pending_create_reminder_confirm() -> None:
    db = _FakeDB()
    skill = ReminderSkill(db_client=db, mcp_client=None, skills_config={})
    remind_time = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    context = SkillContext(
        query="确认",
        user_id="u1",
        extra={
            "pending_action": {
                "action": "create_reminder",
                "payload": {
                    "reminders": [
                        {"content": "开庭提醒（开庭日）", "remind_time": remind_time, "priority": "medium"}
                    ]
                },
            },
            "callback_intent": "confirm",
        },
    )

    result = asyncio.run(skill.execute(context))

    assert result.success is True
    assert result.data.get("clear_pending_action") is True
    assert result.data.get("created_count") == 1
    assert len(db.saved) == 1


def test_reminder_skill_executes_pending_create_reminder_cancel() -> None:
    db = _FakeDB()
    skill = ReminderSkill(db_client=db, mcp_client=None, skills_config={})
    context = SkillContext(
        query="取消",
        user_id="u1",
        extra={
            "pending_action": {"action": "create_reminder", "payload": {"reminders": []}},
            "callback_intent": "cancel",
        },
    )

    result = asyncio.run(skill.execute(context))

    assert result.success is True
    assert result.data.get("clear_pending_action") is True
    assert "取消" in result.reply_text
