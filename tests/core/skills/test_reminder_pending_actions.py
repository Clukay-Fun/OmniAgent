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


# ── S2: pending_action lifecycle and TTL ────────────────────────────

import time

import pytest

from src.core.state.models import PendingActionState, PendingActionStatus  # noqa: E402


def test_pending_action_lifecycle_pending_to_confirmed() -> None:
    now = time.time()
    state = PendingActionState(action="create_record", created_at=now, expires_at=now + 300)
    assert state.status == PendingActionStatus.CONFIRMABLE

    state.transition_to(PendingActionStatus.EXECUTED, now=now)
    assert state.status == PendingActionStatus.EXECUTED


def test_pending_action_lifecycle_pending_to_cancelled() -> None:
    now = time.time()
    state = PendingActionState(action="create_record", created_at=now, expires_at=now + 300)
    state.transition_to(PendingActionStatus.INVALIDATED, now=now)
    assert state.status == PendingActionStatus.INVALIDATED


def test_pending_action_expires_after_ttl() -> None:
    now = time.time()
    state = PendingActionState(action="create_record", created_at=now - 600, expires_at=now - 1)
    # Attempt execute on expired action — should auto-transition to INVALIDATED first
    with pytest.raises(ValueError, match="invalid pending_action transition"):
        state.transition_to(PendingActionStatus.EXECUTED, now=now)
    assert state.status == PendingActionStatus.INVALIDATED


def test_pending_action_confirmed_cannot_reconfirm() -> None:
    now = time.time()
    state = PendingActionState(
        action="create_record", status=PendingActionStatus.EXECUTED,
        created_at=now, expires_at=now + 300,
    )
    with pytest.raises(ValueError, match="invalid pending_action transition"):
        state.transition_to(PendingActionStatus.EXECUTED, now=now)


def test_pending_action_expired_is_terminal() -> None:
    now = time.time()
    state = PendingActionState(
        action="create_record", status=PendingActionStatus.INVALIDATED,
        created_at=now - 600, expires_at=now - 1,
    )
    with pytest.raises(ValueError, match="invalid pending_action transition"):
        state.transition_to(PendingActionStatus.EXECUTED, now=now)


def test_pending_action_status_string_is_normalized() -> None:
    state = PendingActionState(action="create_record", status="confirmed", created_at=0, expires_at=300)  # type: ignore[arg-type]
    assert state.status == PendingActionStatus.EXECUTED


def test_pending_action_operations_are_normalized() -> None:
    state = PendingActionState(
        action="batch_update_records",
        operations=[{"record_id": "rec_1"}, "invalid", {"record_id": "rec_2"}],  # type: ignore[list-item]
        created_at=0,
        expires_at=300,
    )

    assert state.operations == [{"record_id": "rec_1"}, {"record_id": "rec_2"}]
    assert state.iter_operation_payloads() == [{"record_id": "rec_1"}, {"record_id": "rec_2"}]


def test_pending_action_iter_payloads_fallbacks_to_payload_and_payload_operations() -> None:
    state_from_payload_operations = PendingActionState(
        action="batch_delete_records",
        payload={"operations": [{"record_id": "rec_1"}, {"record_id": "rec_2"}]},
        created_at=0,
        expires_at=300,
    )
    assert state_from_payload_operations.iter_operation_payloads() == [
        {"record_id": "rec_1"},
        {"record_id": "rec_2"},
    ]

    state_single = PendingActionState(
        action="update_record",
        payload={"record_id": "rec_single", "fields": {"状态": "已结案"}},
        created_at=0,
        expires_at=300,
    )
    assert state_single.iter_operation_payloads() == [
        {"record_id": "rec_single", "fields": {"状态": "已结案"}}
    ]
