from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.discord.event_adapter import DiscordMessageEvent
from src.api.discord_client import (
    _reply_kwargs,
    extract_user_text,
    is_clear_history_command,
    is_feishu_operation_query,
    should_process_event,
)
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


def test_extract_user_text_strips_mention_even_when_mention_not_required() -> None:
    event = _event(text="<@123> 查案件")

    text = extract_user_text(event, bot_user_id="123", require_mention=False)

    assert text == "查案件"


def test_reply_kwargs_no_reference_and_rich_payload_only_first_chunk() -> None:
    embed = object()
    view = object()

    first = _reply_kwargs("first", include_rich=True, embed=embed, view=view)
    second = _reply_kwargs("second", include_rich=False, embed=embed, view=view)

    assert "reference" not in first
    assert first["content"] == "first"
    assert first["mention_author"] is False
    assert first["embed"] is embed
    assert first["view"] is view

    assert "reference" not in second
    assert second["content"] == "second"
    assert second["mention_author"] is False
    assert "embed" not in second
    assert "view" not in second


def test_is_feishu_operation_query_covers_crud_and_reminder_automation() -> None:
    assert is_feishu_operation_query("帮我查一下张三的案件") is True
    assert is_feishu_operation_query("把案号 A-1 的状态修改为已完成") is True
    assert is_feishu_operation_query("提醒我每周一上午九点开会") is True
    assert is_feishu_operation_query("设置自动化提醒，每天 09:00 推送") is True


def test_is_feishu_operation_query_returns_false_for_general_chat() -> None:
    assert is_feishu_operation_query("你好，今天心情不错") is False
    assert is_feishu_operation_query("今晚吃什么") is False


def test_is_feishu_operation_query_treats_confirm_cancel_as_operation() -> None:
    assert is_feishu_operation_query("确认") is True
    assert is_feishu_operation_query("取消") is True


def test_is_feishu_operation_query_treats_navigation_and_ordinal_as_operation() -> None:
    assert is_feishu_operation_query("下一页") is True
    assert is_feishu_operation_query("继续") is True
    assert is_feishu_operation_query("第6个详情") is True


def test_is_clear_history_command_accepts_private_chat_reset_phrases() -> None:
    assert is_clear_history_command("清空会话") is True
    assert is_clear_history_command(" 清空聊天记录 ") is True
    assert is_clear_history_command("重置记忆。") is True
    assert is_clear_history_command("/reset") is True


def test_is_clear_history_command_rejects_regular_messages() -> None:
    assert is_clear_history_command("帮我查询一下案件") is False
    assert is_clear_history_command("今晚吃什么") is False
