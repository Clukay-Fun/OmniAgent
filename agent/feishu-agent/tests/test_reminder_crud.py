import pytest
from datetime import datetime, timezone

from src.core.skills.reminder import ReminderSkill
from src.core.types import SkillContext


class FakeReminderDB:
    def __init__(self) -> None:
        self.created = []
        self.updated = []
        self.deleted = []

    async def create_reminder(self, **kwargs):
        self.created.append(kwargs)
        return 12

    async def list_reminders(self, user_id: str, status: str = "pending", limit: int = 20):
        return [
            {
                "id": 12,
                "content": "准备材料",
                "due_at": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
                "priority": "high",
                "status": status,
            }
        ]

    async def update_status(self, reminder_id: int, user_id: str, status: str) -> bool:
        self.updated.append((reminder_id, status))
        return True

    async def delete_reminder(self, reminder_id: int, user_id: str) -> bool:
        self.deleted.append(reminder_id)
        return True


@pytest.mark.asyncio
async def test_create_reminder_with_priority() -> None:
    db = FakeReminderDB()
    skill = ReminderSkill(
        db_client=db,
        skills_config={
            "reminder": {
                "priority_keywords": {"high": ["重要"], "low": []}
            }
        },
    )
    context = SkillContext(query="提醒我明天重要会议", user_id="u1")

    result = await skill.execute(context)

    assert result.success is True
    assert result.data["priority"] == "high"
    assert db.created


@pytest.mark.asyncio
async def test_list_reminders() -> None:
    db = FakeReminderDB()
    skill = ReminderSkill(db_client=db)
    context = SkillContext(query="查看提醒", user_id="u1")

    result = await skill.execute(context)

    assert result.success is True
    assert "我的提醒" in result.reply_text


@pytest.mark.asyncio
async def test_update_reminder_done() -> None:
    db = FakeReminderDB()
    skill = ReminderSkill(db_client=db)
    context = SkillContext(query="完成提醒 12", user_id="u1")

    result = await skill.execute(context)

    assert result.success is True
    assert result.data["action"] == "done"


@pytest.mark.asyncio
async def test_update_reminder_missing_id() -> None:
    db = FakeReminderDB()
    skill = ReminderSkill(db_client=db)
    context = SkillContext(query="完成提醒", user_id="u1")

    result = await skill.execute(context)

    assert result.success is False
    assert "提醒编号" in result.reply_text
