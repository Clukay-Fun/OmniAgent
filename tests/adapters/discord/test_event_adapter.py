from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.discord.event_adapter import DiscordEventAdapter, strip_bot_mention


class _Guild:
    id = 1001


class _Channel:
    id = 2002


class _Author:
    id = 3003
    name = "alice"
    display_name = "Alice"
    bot = False


class _Message:
    id = 4004
    guild = _Guild()
    channel = _Channel()
    author = _Author()
    content = "<@12345> 请查询今天的开庭"


def test_from_message_normalizes_discord_fields() -> None:
    event = DiscordEventAdapter.from_message(_Message(), bot_user_id="12345")

    assert event.event_id == "4004"
    assert event.message_id == "4004"
    assert event.channel_id == "2002"
    assert event.guild_id == "1001"
    assert event.chat_type == "group"
    assert event.sender_id == "3003"
    assert event.sender_name == "Alice"
    assert event.sender_is_bot is False
    assert event.mentions_bot is True


def test_strip_bot_mention_removes_both_mention_variants() -> None:
    raw = "<@12345> hello <@!12345> world"
    cleaned = strip_bot_mention(raw, "12345")

    assert cleaned == "hello world"
