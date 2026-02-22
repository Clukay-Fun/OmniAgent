from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.feishu.event_adapter import EventEnvelope, FeishuEventAdapter, MessageEvent
from src.api.event_router import FeishuEventRouter


def _build_message_event(event_type: str = "im.message.receive_v1") -> MessageEvent:
    return MessageEvent(
        event_type=event_type,
        event_id="evt_1",
        message_id="msg_1",
        chat_id="oc_1",
        chat_type="p2p",
        message_type="text",
        content='{"text":"hello"}',
        sender_open_id="ou_1",
        sender_user_id="u_1",
        sender_type="user",
    )


def test_event_router_accepts_message_event() -> None:
    router = FeishuEventRouter(enabled_types=["im.message.receive_v1"])
    envelope = EventEnvelope(event_type="im.message.receive_v1", event_id="evt_1", message=_build_message_event())

    result = router.route(envelope)

    assert result.status == "accepted"
    assert result.reason == "message"


def test_event_router_ignores_unimplemented_event() -> None:
    router = FeishuEventRouter(enabled_types=["calendar.calendar.changed_v4"])
    envelope = EventEnvelope(event_type="calendar.calendar.changed_v4", event_id="evt_2", message=None)

    result = router.route(envelope)

    assert result.status == "ignored"
    assert result.reason == "event_not_implemented"


def test_event_router_handles_record_changed_and_calls_hook() -> None:
    calls: list[dict[str, object]] = []

    class _AutomationEngine:
        def on_record_changed(self, **kwargs) -> None:
            calls.append(kwargs)

    payload = {
        "header": {
            "event_type": "drive.file.bitable_record_changed_v1",
            "event_id": "evt_record_1",
        },
        "event": {
            "app_token": "app_mock_1",
            "table_id": "tbl_mock_1",
            "record_id": "rec_mock_1",
            "changed_fields": {
                "案件状态": {
                    "old": "待处理",
                    "new": "已完成",
                },
                "负责人": {
                    "old": "张三",
                    "new": "李四",
                },
            },
        },
    }
    envelope = FeishuEventAdapter.from_webhook_payload(payload)
    router = FeishuEventRouter(
        enabled_types=["drive.file.bitable_record_changed_v1"],
        automation_engine=_AutomationEngine(),
    )

    result = router.route(envelope)

    assert result.status == "handled"
    assert result.reason == "record_changed"
    assert result.handler == "record_changed"
    assert result.payload == {
        "event_id": "evt_record_1",
        "app_token": "app_mock_1",
        "table_id": "tbl_mock_1",
        "record_id": "rec_mock_1",
        "changed_fields": ["案件状态", "负责人"],
        "automation_hook": "called",
    }
    assert calls == [
        {
            "event_id": "evt_record_1",
            "app_token": "app_mock_1",
            "table_id": "tbl_mock_1",
            "record_id": "rec_mock_1",
            "changed_fields": ["案件状态", "负责人"],
            "raw_event": payload["event"],
        }
    ]


def test_event_router_ignores_disabled_event_type() -> None:
    router = FeishuEventRouter(enabled_types=["im.message.receive_v1"])
    envelope = EventEnvelope(event_type="calendar.calendar.changed_v4", event_id="evt_3", message=None)

    result = router.route(envelope)

    assert result.status == "ignored"
    assert result.reason == "event_type_disabled"
