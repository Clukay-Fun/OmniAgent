"""
LLM provider helpers.
"""

from __future__ import annotations

from src.config import LLMSettings
from src.llm.client import LLMClient


def create_llm_client(settings: LLMSettings) -> LLMClient:
    return LLMClient(settings)
