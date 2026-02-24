from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.orchestrator import AgentOrchestrator  # noqa: E402


class _FakeStateManager:
    def __init__(self) -> None:
        self.pending = SimpleNamespace(action="delete_record", payload={"record_id": "rec_1"})

    def get_pending_action(self, _user_id: str):
        return self.pending


class _FakeRouter:
    def get_skill(self, _name: str):
        raise AssertionError("skill should not be invoked on callback mismatch")


def test_callback_action_mismatch_returns_expired() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._state_manager = _FakeStateManager()
    orchestrator._router = _FakeRouter()

    result = asyncio.run(orchestrator.handle_card_action_callback("u1", "update_record_confirm"))

    assert result["status"] == "expired"
    assert "过期" in result["text"]


def test_query_list_navigation_callback_uses_existing_pending_action_route() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._state_manager = SimpleNamespace(
        get_pending_action=lambda _user_id: SimpleNamespace(
            action="query_list_navigation",
            payload={
                "callbacks": {
                    "query_list_today_hearing": {
                        "callback_action": "query_list_today_hearing",
                        "kind": "query",
                        "query": "今天开庭",
                    }
                }
            },
        ),
    )

    captured: dict[str, str] = {}

    class _FakeQuerySkill:
        async def execute(self, context):
            captured["query"] = context.query
            return SimpleNamespace(success=True, skill_name="QuerySkill", data={}, reply_text="查询成功", message="")

    orchestrator._router = SimpleNamespace(get_skill=lambda name: _FakeQuerySkill() if name == "QuerySkill" else None)
    orchestrator._sync_state_after_result = lambda *_args, **_kwargs: None
    orchestrator._response_renderer = SimpleNamespace(
        render=lambda _result: SimpleNamespace(text_fallback="查询成功", to_dict=lambda: {"text_fallback": "查询成功"})
    )
    orchestrator._reply_personalization_enabled = False

    result = asyncio.run(orchestrator.handle_card_action_callback("u1", "query_list_today_hearing"))

    assert result["status"] == "processed"
    assert captured["query"] == "今天开庭"


def test_query_list_navigation_no_more_returns_processed_text() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._state_manager = SimpleNamespace(
        get_pending_action=lambda _user_id: SimpleNamespace(
            action="query_list_navigation",
            payload={
                "callbacks": {
                    "query_list_next_page": {
                        "callback_action": "query_list_next_page",
                        "kind": "no_more",
                        "text": "已经是最后一页了。",
                    }
                }
            },
        ),
    )
    orchestrator._router = SimpleNamespace(get_skill=lambda _name: None)

    result = asyncio.run(orchestrator.handle_card_action_callback("u1", "query_list_next_page"))

    assert result == {"status": "processed", "text": "已经是最后一页了。"}


def test_create_reminder_callback_routes_to_reminder_skill() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._state_manager = SimpleNamespace(
        get_pending_action=lambda _user_id: SimpleNamespace(
            action="create_reminder",
            payload={"reminders": [{"content": "开庭提醒", "remind_time": "2099-01-10 09:00"}]},
        ),
    )

    captured: dict[str, str] = {}

    class _FakeReminderSkill:
        async def execute(self, context):
            captured["query"] = context.query
            return SimpleNamespace(success=True, skill_name="ReminderSkill", data={}, reply_text="已创建提醒", message="")

    orchestrator._router = SimpleNamespace(get_skill=lambda name: _FakeReminderSkill() if name == "ReminderSkill" else None)
    orchestrator._sync_state_after_result = lambda *_args, **_kwargs: None
    orchestrator._response_renderer = SimpleNamespace(
        render=lambda _result: SimpleNamespace(text_fallback="已创建提醒", to_dict=lambda: {"text_fallback": "已创建提醒"})
    )
    orchestrator._reply_personalization_enabled = False

    result = asyncio.run(orchestrator.handle_card_action_callback("u1", "create_reminder_confirm"))

    assert result["status"] == "processed"
    assert captured["query"] == "确认"


def test_close_record_callback_routes_to_update_skill() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._state_manager = SimpleNamespace(
        get_pending_action=lambda _user_id: SimpleNamespace(
            action="close_record",
            payload={
                "record_id": "rec_9",
                "fields": {"案件状态": "已结案"},
                "table_id": "tbl_main",
            },
        ),
    )

    captured: dict[str, str] = {}

    class _FakeUpdateSkill:
        async def execute(self, context):
            captured["query"] = context.query
            return SimpleNamespace(success=True, skill_name="UpdateSkill", data={}, reply_text="已关闭", message="")

    orchestrator._router = SimpleNamespace(get_skill=lambda name: _FakeUpdateSkill() if name == "UpdateSkill" else None)
    orchestrator._sync_state_after_result = lambda *_args, **_kwargs: None
    orchestrator._response_renderer = SimpleNamespace(
        render=lambda _result: SimpleNamespace(text_fallback="已关闭", to_dict=lambda: {"text_fallback": "已关闭"})
    )
    orchestrator._reply_personalization_enabled = False

    result = asyncio.run(orchestrator.handle_card_action_callback("u1", "close_record_confirm"))

    assert result["status"] == "processed"
    assert captured["query"] == "确认"
