"""
描述: 提供意图解析功能的模块
主要功能:
    - 提供IntentParser类用于解析用户意图
    - 提供IntentResult类用于存储解析结果
    - 提供SkillMatch类用于匹配技能
    - 提供load_skills_config函数用于加载技能配置
"""

from __future__ import annotations

from src.core.intent.parser import IntentParser, IntentResult, SkillMatch, load_skills_config

__all__ = [
    "IntentParser",
    "IntentResult",
    "SkillMatch",
    "load_skills_config",
]
