"""
描述: 技能路由模块的核心职责是管理技能的路由逻辑。
主要功能:
    - 提供技能路由的基本结构和上下文管理。
    - 定义技能路由所需的模型和决策规则。
"""

from __future__ import annotations

from src.core.understanding.router.router import ContextManager, SkillRouter
from src.core.understanding.router.model_routing import (
    ComplexityScore,
    ModelRouter,
    RoutingDecision,
    RuleBasedComplexityScorer,
)
from src.core.foundation.common.types import SkillContext, SkillResult
from src.core.capabilities.skills.base.base import BaseSkill

__all__ = [
    "BaseSkill",
    "ContextManager",
    "ComplexityScore",
    "ModelRouter",
    "SkillContext",
    "SkillResult",
    "RoutingDecision",
    "RuleBasedComplexityScorer",
    "SkillRouter",
]
