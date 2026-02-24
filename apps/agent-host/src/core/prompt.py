"""
描述: Prompt 模板构建工具
主要功能:
    - 组装 System Prompt
    - 处理 Role, Capabilities, Constraints 等配置
"""

from __future__ import annotations

from src.config import PromptSettings


# region 系统提示词构建
def build_system_prompt(settings: PromptSettings) -> str:
    """
    构建 LLM 系统提示词 (System Prompt)
    
    参数:
        settings: Prompt 配置对象
    
    返回:
        完整的 Prompt 字符串
    """
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
# endregion
