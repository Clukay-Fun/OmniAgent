"""
描述: 技能基类定义
主要功能:
    - 定义标准技能接口 (match/execute)
    - 提供基础属性 (name, description)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.foundation.common.types import SkillContext, SkillResult


# region 技能基类
class BaseSkill:
    """
    所有技能的抽象基类
    
    属性:
        name: 技能名称 (唯一标识)
        description: 技能描述 (用于路由/帮助)
        keywords: 关键词列表 (可选，用于简单匹配)
    """
    name: str = "BaseSkill"
    description: str = ""
    keywords: list[str] = []

    async def execute(self, context: "SkillContext") -> "SkillResult":
        """
        [标准入口] 执行技能逻辑
        
        参数:
            context: 技能执行上下文
            
        返回:
            SkillResult: 执行结果
        """
        return await self.run(context.query, context)

    def match(self, query: str, context: "SkillContext") -> float:
        """
        [可选] 计算匹配度
        
        返回:
            0.0 ~ 1.0 的置信度
        """
        return 0.0

    async def run(self, query: str, context: "SkillContext") -> "SkillResult":
        """
        [内部接口] 实际执行逻辑 (将被废弃，推荐实现 execute)
        """
        raise NotImplementedError("Subclass must implement run()")
# endregion
