from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[4]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.feishu.event_adapter import FeishuEventAdapter


def test_from_webhook_payload_extracts_message_event() -> None:
    payload = {
        "header": {
            "event_type": "im.message.receive_v1",
            "event_id": "evt_1",
        },
        "event": {
            "message": {
                "message_id": "omni_msg_1",
                "chat_id": "oc_123",
                "chat_type": "p2p",
                "message_type": "text",
                "content": '{"text":"你好"}',
            },
            "sender": {
                "sender_type": "user",
                "sender_id": {
                    "open_id": "ou_123",
                    "user_id": "u_123",
                },
            },
        },
    }

    envelope = FeishuEventAdapter.from_webhook_payload(payload)

    assert envelope.event_type == "im.message.receive_v1"
    assert envelope.event_id == "evt_1"
    assert envelope.message is not None
    assert envelope.message.message_id == "omni_msg_1"
    assert envelope.message.sender_open_id == "ou_123"
    assert isinstance(envelope.event, dict)


def test_from_ws_event_extracts_message_event() -> None:
    class SenderId:
        open_id = "ou_ws"
        user_id = "u_ws"

    class Sender:
        sender_id = SenderId()
        sender_type = "user"

    class Message:
        message_id = "m_ws"
        chat_id = "oc_ws"
        chat_type = "p2p"
        message_type = "text"
        content = '{"text":"ws"}'

    class Event:
        message = Message()
        sender = Sender()

    class Data:
        event = Event()

    message_event = FeishuEventAdapter.from_ws_event(Data())

    assert message_event is not None
    assert message_event.message_id == "m_ws"
    assert message_event.chat_id == "oc_ws"
    assert message_event.sender_open_id == "ou_ws"
