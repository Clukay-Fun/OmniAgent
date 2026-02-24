from __future__ import annotations


def map_target_conversation_id(target_conversation_id: str) -> tuple[str, str] | None:
    conversation_id = str(target_conversation_id or "").strip()
    if not conversation_id:
        return None
    return conversation_id, "chat_id"
