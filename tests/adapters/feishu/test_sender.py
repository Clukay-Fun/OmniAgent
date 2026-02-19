from pathlib import Path
import sys
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "apps" / "agent-host" / "src"
sys.path.insert(0, str(SRC))

from adapters.channels.feishu.sender import FeishuSender
from core.response.models import Block, RenderedResponse


def test_sender_calls_formatter_once_and_passes_payload_through() -> None:
    payload = {"msg_type": "text", "content": {"text": "payload"}}
    send_result = {"ok": True, "id": "message-id"}

    formatter = Mock()
    formatter.format.return_value = payload
    send_callable = Mock(return_value=send_result)

    sender = FeishuSender(send_callable=send_callable, formatter=formatter)
    rendered = RenderedResponse(
        text_fallback="fallback",
        blocks=[Block(type="paragraph", content={"text": "hello"})],
    )

    result = sender.send(rendered)

    formatter.format.assert_called_once_with(rendered)
    send_callable.assert_called_once_with(payload)
    assert result == send_result
