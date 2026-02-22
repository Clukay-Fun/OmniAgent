"""会话作用域辅助函数。"""

from __future__ import annotations


def build_conversation_user_id(user_id: str, chat_id: str | None, chat_type: str | None) -> str:
    """
    生成会话作用域用户键。

    - 群聊: group:{chat_id}:user:{user_id}
    - 私聊及其他: user_id
    """
    base_user_id = str(user_id or "").strip() or "unknown"
    normalized_chat_type = str(chat_type or "").strip().lower()
    normalized_chat_id = str(chat_id or "").strip()
    if normalized_chat_type == "group" and normalized_chat_id:
        return f"group:{normalized_chat_id}:user:{base_user_id}"
    return base_user_id
