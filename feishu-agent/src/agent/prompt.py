"""
Prompt templates.
"""

from __future__ import annotations

from src.config import PromptSettings


def build_system_prompt(settings: PromptSettings) -> str:
    parts = []
    if settings.role:
        parts.append(settings.role.strip())
    if settings.capabilities:
        parts.append("能力：\n" + settings.capabilities.strip())
    if settings.constraints:
        parts.append("限制：\n" + settings.constraints.strip())
    if settings.output_format:
        parts.append("输出格式：\n" + settings.output_format.strip())
    return "\n\n".join(parts)
