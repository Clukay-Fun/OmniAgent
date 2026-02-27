"""
描述: 提供一个函数来映射目标对话ID到特定的格式
主要功能:
    - 将目标对话ID映射为一个包含对话ID和固定字符串"chat_id"的元组
    - 如果目标对话ID为空或无效，则返回None
"""

def map_target_conversation_id(target_conversation_id: str) -> tuple[str, str] | None:
    """
    将目标对话ID映射为一个包含对话ID和固定字符串"chat_id"的元组

    功能:
        - 去除目标对话ID的前后空白字符
        - 检查处理后的对话ID是否为空，若为空则返回None
        - 返回一个包含处理后的对话ID和字符串"chat_id"的元组
    """
    conversation_id = str(target_conversation_id or "").strip()
    if not conversation_id:
        return None
    return conversation_id, "chat_id"
