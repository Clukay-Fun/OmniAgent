from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.feishu.event_adapter import EventEnvelope, MessageEvent
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
    router = FeishuEventRouter(enabled_types=["drive.file.bitable_record_changed_v1"])
    envelope = EventEnvelope(event_type="drive.file.bitable_record_changed_v1", event_id="evt_2", message=None)

    result = router.route(envelope)

    assert result.status == "ignored"
    assert result.reason == "event_not_implemented"


def test_event_router_ignores_disabled_event_type() -> None:
    router = FeishuEventRouter(enabled_types=["im.message.receive_v1"])
    envelope = EventEnvelope(event_type="calendar.calendar.changed_v4", event_id="evt_3", message=None)

    result = router.route(envelope)

    assert result.status == "ignored"
    assert result.reason == "event_type_disabled"
