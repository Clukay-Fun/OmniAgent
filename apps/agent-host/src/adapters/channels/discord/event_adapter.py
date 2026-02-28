"""
描述: Discord 事件适配器
主要功能:
    - 将 Discord message 对象标准化为通道无关事件
    - 统一提取 user/channel/guild/chat_type 字段
    - 处理 @mention 标记和基础文本清洗
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class DiscordMessageEvent:
    """标准化后的 Discord 消息事件。"""

    event_id: str
    message_id: str
    channel_id: str
    guild_id: str
    chat_type: str
    text: str
    sender_id: str
    sender_name: str
    sender_is_bot: bool
    mentions_bot: bool


def strip_bot_mention(text: str, bot_user_id: str) -> str:
    """移除 Discord 文本中的 bot mention 标记。"""

    raw_text = str(text or "")
    mention_a = f"<@{bot_user_id}>"
    mention_b = f"<@!{bot_user_id}>"
    cleaned = raw_text.replace(mention_a, " ").replace(mention_b, " ")
    return " ".join(cleaned.split())


class DiscordEventAdapter:
    """Discord 事件适配器。"""

    @staticmethod
    def from_message(message: Any, *, bot_user_id: str = "") -> DiscordMessageEvent:
        """从 discord.Message 提取标准化事件。"""

        message_id = str(getattr(message, "id", "") or "").strip()
        channel = getattr(message, "channel", None)
        channel_id = str(getattr(channel, "id", "") or "").strip()

        guild = getattr(message, "guild", None)
        guild_id = str(getattr(guild, "id", "") or "").strip()
        chat_type = "group" if guild_id else "p2p"

        author = getattr(message, "author", None)
        sender_id = str(getattr(author, "id", "") or "").strip()
        sender_name = str(getattr(author, "display_name", "") or getattr(author, "name", "") or "").strip()
        sender_is_bot = bool(getattr(author, "bot", False))

        raw_text = str(getattr(message, "content", "") or "")
        mentions_bot = False
        if bot_user_id:
            mentions_bot = f"<@{bot_user_id}>" in raw_text or f"<@!{bot_user_id}>" in raw_text

        event_id = message_id
        return DiscordMessageEvent(
            event_id=event_id,
            message_id=message_id,
            channel_id=channel_id,
            guild_id=guild_id,
            chat_type=chat_type,
            text=raw_text,
            sender_id=sender_id,
            sender_name=sender_name,
            sender_is_bot=sender_is_bot,
            mentions_bot=mentions_bot,
        )
