from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.orchestrator import AgentOrchestrator  # noqa: E402
from src.core.errors import (  # noqa: E402
    PendingActionExpiredError,
    PendingActionNotFoundError,
    get_user_message,
)
from src.core.state.models import OperationEntry, OperationExecutionStatus, PendingActionState  # noqa: E402


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
    assert result["text"] == get_user_message(PendingActionExpiredError())


def test_callback_without_pending_action_returns_catalog_message() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._state_manager = SimpleNamespace(get_pending_action=lambda _user_id: None)

    result = asyncio.run(orchestrator.handle_card_action_callback("u1", "update_record_confirm"))

    assert result["status"] == "expired"
    assert result["text"] == get_user_message(PendingActionNotFoundError())


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


def test_edit_callback_routes_to_update_skill_with_active_record() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    record = {
        "record_id": "rec_edit_1",
        "fields_text": {
            "项目ID": "JFTD-20260001",
            "委托人": "香港华艺设计顾问",
            "对方当事人": "广州荔富汇景",
        },
    }
    orchestrator._state_manager = SimpleNamespace(
        get_pending_action=lambda _user_id: None,
        get_active_record=lambda _user_id: SimpleNamespace(
            record_id="rec_edit_1",
            record=record,
            table_id="tbl_main",
            table_name="案件项目总库",
        ),
        get_last_result=lambda _user_id: None,
    )

    captured: dict[str, str] = {}

    class _FakeUpdateSkill:
        async def execute(self, context):
            captured["query"] = context.query
            active_record = context.extra.get("active_record") if isinstance(context.extra, dict) else {}
            captured["record_id"] = str((active_record or {}).get("record_id") or "")
            return SimpleNamespace(
                success=True,
                skill_name="UpdateSkill",
                data={"pending_action": {"action": "update_collect_fields", "payload": {"record_id": "rec_edit_1"}}},
                reply_text="已定位到案件，请告诉我要修改什么。",
                message="",
            )

    orchestrator._router = SimpleNamespace(get_skill=lambda name: _FakeUpdateSkill() if name == "UpdateSkill" else None)
    orchestrator._sync_state_after_result = lambda *_args, **_kwargs: None
    orchestrator._response_renderer = SimpleNamespace(
        render=lambda _result: SimpleNamespace(
            text_fallback="已定位到案件，请告诉我要修改什么。",
            to_dict=lambda: {"text_fallback": "已定位到案件，请告诉我要修改什么。"},
        )
    )
    orchestrator._reply_personalization_enabled = False

    result = asyncio.run(
        orchestrator.handle_card_action_callback(
            "u1",
            "edit",
            callback_value={"record_id": "rec_edit_1", "table_type": "case"},
        )
    )

    assert result["status"] == "processed"
    assert captured["query"] == "修改该案件内容"
    assert captured["record_id"] == "rec_edit_1"


def test_callback_skill_failure_still_returns_processed_with_error_outbound() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._state_manager = SimpleNamespace(
        get_pending_action=lambda _user_id: SimpleNamespace(
            action="update_record",
            payload={"record_id": "rec_missing", "fields": {"案号": "A-1"}, "source_fields": {"案号": "A-0"}},
        )
    )

    class _FakeUpdateSkill:
        async def execute(self, _context):
            return SimpleNamespace(success=False, skill_name="UpdateSkill", data={}, reply_text="目标记录不存在", message="")

    orchestrator._router = SimpleNamespace(get_skill=lambda name: _FakeUpdateSkill() if name == "UpdateSkill" else None)
    orchestrator._sync_state_after_result = lambda *_args, **_kwargs: None
    orchestrator._response_renderer = SimpleNamespace(
        render=lambda _result: SimpleNamespace(text_fallback="目标记录不存在", to_dict=lambda: {"text_fallback": "目标记录不存在"})
    )
    orchestrator._reply_personalization_enabled = False

    result = asyncio.run(orchestrator.handle_card_action_callback("u1", "update_record_confirm"))

    assert result["status"] == "processed"
    assert "不存在" in result["text"]


def test_callback_confirm_triggers_pending_action_transition_on_success() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)

    class _FakeStateManager:
        def __init__(self) -> None:
            self.confirmed = 0
            self.cancelled = 0

        def get_pending_action(self, _user_id: str):
            return SimpleNamespace(action="create_reminder", payload={"reminders": []})

        def confirm_pending_action(self, _user_id: str):
            self.confirmed += 1

        def cancel_pending_action(self, _user_id: str):
            self.cancelled += 1

    state_manager = _FakeStateManager()
    orchestrator._state_manager = state_manager

    class _FakeReminderSkill:
        async def execute(self, _context):
            return SimpleNamespace(success=True, skill_name="ReminderSkill", data={}, reply_text="ok", message="")

    orchestrator._router = SimpleNamespace(get_skill=lambda name: _FakeReminderSkill() if name == "ReminderSkill" else None)
    orchestrator._sync_state_after_result = lambda *_args, **_kwargs: None
    orchestrator._response_renderer = SimpleNamespace(
        render=lambda _result: SimpleNamespace(text_fallback="ok", to_dict=lambda: {"text_fallback": "ok"})
    )
    orchestrator._reply_personalization_enabled = False

    result = asyncio.run(orchestrator.handle_card_action_callback("u1", "create_reminder_confirm"))

    assert result["status"] == "processed"
    assert state_manager.confirmed == 1
    assert state_manager.cancelled == 0


def test_batch_update_callback_confirm_executes_all_operations_in_order() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)

    class _FakeStateManager:
        def __init__(self) -> None:
            self.confirmed = 0

        def get_pending_action(self, _user_id: str):
            return SimpleNamespace(
                action="batch_update_records",
                payload={},
                operations=[
                    {"action": "update_record", "record_id": "rec_1", "fields": {"进展": "已联系"}},
                    {"action": "update_record", "record_id": "rec_2", "fields": {"进展": "已补证"}},
                ],
            )

        def confirm_pending_action(self, _user_id: str):
            self.confirmed += 1

    state_manager = _FakeStateManager()
    orchestrator._state_manager = state_manager

    executed_record_ids: list[str] = []

    class _FakeUpdateSkill:
        async def execute(self, context):
            pending_action_raw = context.extra.get("pending_action") if isinstance(context.extra, dict) else {}
            pending_action = pending_action_raw if isinstance(pending_action_raw, dict) else {}
            payload_raw = pending_action.get("payload")
            payload = payload_raw if isinstance(payload_raw, dict) else {}
            executed_record_ids.append(str(payload.get("record_id") or ""))
            return SimpleNamespace(success=True, skill_name="UpdateSkill", data={}, reply_text="ok", message="")

    orchestrator._router = SimpleNamespace(get_skill=lambda name: _FakeUpdateSkill() if name == "UpdateSkill" else None)
    orchestrator._sync_state_after_result = lambda *_args, **_kwargs: None
    orchestrator._response_renderer = SimpleNamespace(
        render=lambda _result: SimpleNamespace(text_fallback="ok", to_dict=lambda: {"text_fallback": "ok"})
    )
    orchestrator._reply_personalization_enabled = False

    result = asyncio.run(orchestrator.handle_card_action_callback("u1", "batch_update_records_confirm"))

    assert result["status"] == "processed"
    assert executed_record_ids == ["rec_1", "rec_2"]
    assert state_manager.confirmed == 1


def test_batch_update_callback_stops_on_first_failed_operation() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)

    class _FakeStateManager:
        def __init__(self) -> None:
            self.confirmed = 0

        def get_pending_action(self, _user_id: str):
            return SimpleNamespace(
                action="batch_update_records",
                payload={},
                operations=[
                    {"action": "update_record", "record_id": "rec_1", "fields": {"进展": "已联系"}},
                    {"action": "update_record", "record_id": "rec_2", "fields": {"进展": "已补证"}},
                ],
            )

        def confirm_pending_action(self, _user_id: str):
            self.confirmed += 1

    state_manager = _FakeStateManager()
    orchestrator._state_manager = state_manager

    executed_record_ids: list[str] = []

    class _FakeUpdateSkill:
        async def execute(self, context):
            pending_action_raw = context.extra.get("pending_action") if isinstance(context.extra, dict) else {}
            pending_action = pending_action_raw if isinstance(pending_action_raw, dict) else {}
            payload_raw = pending_action.get("payload")
            payload = payload_raw if isinstance(payload_raw, dict) else {}
            current_record_id = str(payload.get("record_id") or "")
            executed_record_ids.append(current_record_id)
            return SimpleNamespace(
                success=current_record_id != "rec_1",
                skill_name="UpdateSkill",
                data={},
                reply_text="目标记录不存在",
                message="",
            )

    orchestrator._router = SimpleNamespace(get_skill=lambda name: _FakeUpdateSkill() if name == "UpdateSkill" else None)
    orchestrator._sync_state_after_result = lambda *_args, **_kwargs: None
    orchestrator._response_renderer = SimpleNamespace(
        render=lambda _result: SimpleNamespace(
            text_fallback="目标记录不存在",
            to_dict=lambda: {"text_fallback": "目标记录不存在"},
        )
    )
    orchestrator._reply_personalization_enabled = False

    result = asyncio.run(orchestrator.handle_card_action_callback("u1", "batch_update_records_confirm"))

    assert result["status"] == "processed"
    assert "不存在" in result["text"]
    assert executed_record_ids == ["rec_1"]
    assert state_manager.confirmed == 0


def test_batch_update_callback_persists_per_operation_status_after_failure() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    pending = PendingActionState(
        action="batch_update_records",
        operations=[
            OperationEntry(index=0, payload={"action": "update_record", "record_id": "rec_1", "fields": {"进展": "已联系"}}),
            OperationEntry(index=1, payload={"action": "update_record", "record_id": "rec_2", "fields": {"进展": "已补证"}}),
        ],
        created_at=1.0,
        expires_at=9999999999.0,
    )

    class _FakeStateManager:
        def __init__(self) -> None:
            self.confirmed = 0
            self.updated = 0

        def get_pending_action(self, _user_id: str):
            return pending

        def confirm_pending_action(self, _user_id: str):
            self.confirmed += 1

        def update_pending_action_operations(self, _user_id: str, _pending: PendingActionState):
            self.updated += 1
            return _pending

    state_manager = _FakeStateManager()
    orchestrator._state_manager = state_manager

    class _FakeUpdateSkill:
        async def execute(self, context):
            pending_action_raw = context.extra.get("pending_action") if isinstance(context.extra, dict) else {}
            payload_raw = pending_action_raw.get("payload") if isinstance(pending_action_raw, dict) else {}
            payload = payload_raw if isinstance(payload_raw, dict) else {}
            record_id = str(payload.get("record_id") or "")
            return SimpleNamespace(
                success=record_id != "rec_1",
                skill_name="UpdateSkill",
                data={},
                reply_text="目标记录不存在" if record_id == "rec_1" else "ok",
                message="",
            )

    orchestrator._router = SimpleNamespace(get_skill=lambda name: _FakeUpdateSkill() if name == "UpdateSkill" else None)
    orchestrator._sync_state_after_result = lambda *_args, **_kwargs: None
    orchestrator._response_renderer = SimpleNamespace(
        render=lambda result: SimpleNamespace(text_fallback=result.reply_text, to_dict=lambda: {"text_fallback": result.reply_text})
    )
    orchestrator._reply_personalization_enabled = False

    result = asyncio.run(orchestrator.handle_card_action_callback("u1", "batch_update_records_confirm"))

    assert result["status"] == "processed"
    assert "失败" in result["text"]
    assert state_manager.confirmed == 0
    assert state_manager.updated == 1
    assert pending.operations[0].status == OperationExecutionStatus.FAILED
    assert pending.operations[1].status == OperationExecutionStatus.SKIPPED


def test_batch_update_callback_retry_executes_failed_and_skipped_only() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    pending = PendingActionState(
        action="batch_update_records",
        operations=[
            OperationEntry(
                index=0,
                payload={"action": "update_record", "record_id": "rec_1", "fields": {"进展": "已联系"}},
                status=OperationExecutionStatus.SUCCEEDED,
            ),
            OperationEntry(
                index=1,
                payload={"action": "update_record", "record_id": "rec_2", "fields": {"进展": "已补证"}},
                status=OperationExecutionStatus.FAILED,
                error_code="update_record_failed",
            ),
            OperationEntry(
                index=2,
                payload={"action": "update_record", "record_id": "rec_3", "fields": {"进展": "已回访"}},
                status=OperationExecutionStatus.SKIPPED,
            ),
        ],
        created_at=1.0,
        expires_at=9999999999.0,
    )

    class _FakeStateManager:
        def __init__(self) -> None:
            self.confirmed = 0
            self.updated = 0

        def get_pending_action(self, _user_id: str):
            return pending

        def confirm_pending_action(self, _user_id: str):
            self.confirmed += 1

        def update_pending_action_operations(self, _user_id: str, _pending: PendingActionState):
            self.updated += 1
            return _pending

    state_manager = _FakeStateManager()
    orchestrator._state_manager = state_manager

    executed_record_ids: list[str] = []

    class _FakeUpdateSkill:
        async def execute(self, context):
            pending_action_raw = context.extra.get("pending_action") if isinstance(context.extra, dict) else {}
            payload_raw = pending_action_raw.get("payload") if isinstance(pending_action_raw, dict) else {}
            payload = payload_raw if isinstance(payload_raw, dict) else {}
            record_id = str(payload.get("record_id") or "")
            executed_record_ids.append(record_id)
            return SimpleNamespace(success=True, skill_name="UpdateSkill", data={}, reply_text="ok", message="")

    orchestrator._router = SimpleNamespace(get_skill=lambda name: _FakeUpdateSkill() if name == "UpdateSkill" else None)
    orchestrator._sync_state_after_result = lambda *_args, **_kwargs: None
    orchestrator._response_renderer = SimpleNamespace(
        render=lambda result: SimpleNamespace(text_fallback=result.reply_text, to_dict=lambda: {"text_fallback": result.reply_text})
    )
    orchestrator._reply_personalization_enabled = False

    result = asyncio.run(orchestrator.handle_card_action_callback("u1", "batch_update_records_retry"))

    assert result["status"] == "processed"
    assert state_manager.confirmed == 1
    assert state_manager.updated == 1
    assert executed_record_ids == ["rec_2", "rec_3"]
    assert all(item.status == OperationExecutionStatus.SUCCEEDED for item in pending.operations)
