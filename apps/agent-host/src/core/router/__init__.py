"""
描述: 技能路由模块的核心职责是管理技能的路由逻辑。
主要功能:
    - 提供技能路由的基本结构和上下文管理。
    - 定义技能路由所需的模型和决策规则。
"""

from src.core.router.router import ContextManager, SkillRouter
from src.core.router.model_routing import (
    ComplexityScore,
    ModelRouter,
    RoutingDecision,
    RuleBasedComplexityScorer,
)
from src.core.types import SkillContext, SkillResult
from src.core.skills.base import BaseSkill

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

# region 类和模型定义
class BaseSkill:
    """
    技能基类，定义了技能的基本结构。

    功能:
        - 提供技能的基本接口。
        - 定义技能的上下文和结果类型。
    """

class ContextManager:
    """
    上下文管理器，负责管理技能执行的上下文。

    功能:
        - 创建和维护技能执行的上下文。
        - 提供上下文相关的操作方法。
    """

class SkillRouter:
    """
    技能路由器，负责将请求路由到相应的技能。

    功能:
        - 根据请求的特征选择合适的技能。
        - 管理技能的执行流程。
    """

class ModelRouter:
    """
    模型路由器，负责将请求路由到相应的模型。

    功能:
        - 根据请求的复杂度选择合适的模型。
        - 管理模型的执行流程。
    """

class ComplexityScore:
    """
    复杂度评分，用于评估请求的复杂度。

    功能:
        - 计算请求的复杂度评分。
        - 提供复杂度评分的比较和排序。
    """

class RuleBasedComplexityScorer:
    """
    规则基础的复杂度评分器，基于预定义的规则进行复杂度评分。

    功能:
        - 根据预定义的规则计算请求的复杂度评分。
        - 提供灵活的规则配置和调整。
    """

class RoutingDecision:
    """
    路由决策，用于存储路由选择的结果。

    功能:
        - 存储路由选择的结果信息。
        - 提供路由决策的查询和操作方法。
    """
# endregion

# region 类型定义
class SkillContext:
    """
    技能上下文，包含技能执行所需的所有信息。

    功能:
        - 存储技能执行的上下文信息。
        - 提供上下文信息的访问和修改方法。
    """

class SkillResult:
    """
    技能结果，包含技能执行的结果信息。

    功能:
        - 存储技能执行的结果信息。
        - 提供结果信息的访问和修改方法。
    """
# endregion
