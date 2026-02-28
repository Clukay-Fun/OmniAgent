from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.discord.event_adapter import DiscordMessageEvent
from src.api.discord_client import extract_user_text, should_process_event
from src.config import DiscordSettings


def _event(**overrides: object) -> DiscordMessageEvent:
    payload = {
        "event_id": "evt_1",
        "message_id": "msg_1",
        "channel_id": "ch_1",
        "guild_id": "guild_1",
        "chat_type": "group",
        "text": "<@123> 你好",
        "sender_id": "user_1",
        "sender_name": "User 1",
        "sender_is_bot": False,
        "mentions_bot": True,
    }
    payload.update(overrides)
    return DiscordMessageEvent(**payload)


def test_should_process_event_requires_mention_in_group() -> None:
    config = DiscordSettings(require_mention=True, private_chat_only=False)

    assert should_process_event(_event(mentions_bot=True), config=config) is True
    assert should_process_event(_event(mentions_bot=False), config=config) is False


def test_should_process_event_respects_private_chat_only_and_allow_bots() -> None:
    config = DiscordSettings(private_chat_only=True, allow_bots=False)

    assert should_process_event(_event(chat_type="group"), config=config) is False
    assert should_process_event(_event(chat_type="p2p", sender_is_bot=True), config=config) is False
    assert should_process_event(_event(chat_type="p2p", sender_is_bot=False), config=config) is True


def test_extract_user_text_strips_mention_when_required() -> None:
    event = _event(text="<@123>   请帮我查一下")

    text = extract_user_text(event, bot_user_id="123", require_mention=True)

    assert text == "请帮我查一下"
