"""
描述: 提供会话作用域辅助函数，用于生成会话键。
主要功能:
    - 生成会话作用域用户键
    - 提供兼容别名函数
"""

from __future__ import annotations


# region 会话键生成函数
def build_session_key(
    user_id: str,
    chat_id: str | None,
    chat_type: str | None,
    channel_type: str | None = None,
) -> str:
    """
    生成会话作用域用户键。

    功能:
        - 根据用户ID、聊天ID、聊天类型和频道类型生成会话键
        - 对于群聊，格式为 {channel_type}:group:{chat_id}:user:{user_id}
        - 对于私聊及其他情况，直接返回 user_id
    """
    base_user_id = str(user_id or "").strip() or "unknown"
    normalized_chat_type = str(chat_type or "").strip().lower()
    normalized_chat_id = str(chat_id or "").strip()
    normalized_channel = str(channel_type or "").strip().lower() or "unknown"
    if normalized_chat_type == "group" and normalized_chat_id:
        return f"{normalized_channel}:group:{normalized_chat_id}:user:{base_user_id}"
    return base_user_id


def build_conversation_user_id(user_id: str, chat_id: str | None, chat_type: str | None) -> str:
    """
    兼容别名：等价于 build_session_key。

    功能:
        - 调用 build_session_key 函数生成会话键，固定 channel_type 为 "feishu"
    """
    return build_session_key(user_id=user_id, chat_id=chat_id, chat_type=chat_type, channel_type="feishu")
# endregion
