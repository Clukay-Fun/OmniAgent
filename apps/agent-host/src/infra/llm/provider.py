"""
描述: 提供LLM客户端的辅助函数。
主要功能:
    - 创建LLM客户端实例
"""

from __future__ import annotations

from src.config import LLMSettings
from src.llm.client import LLMClient

# region 创建LLM客户端
def create_llm_client(settings: LLMSettings) -> LLMClient:
    """
    创建并返回一个LLM客户端实例。

    功能:
        - 根据传入的设置创建LLMClient实例
    """
    return LLMClient(settings)
# endregion
