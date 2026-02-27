"""
描述: 提供L0规则层的核心类和功能。
主要功能:
    - 定义L0决策类
    - 提供L0规则引擎类
"""

from src.core.l0.engine import L0Decision, L0RuleEngine

__all__ = ["L0Decision", "L0RuleEngine"]

# region 类定义
class L0Decision:
    """
    L0决策类，用于封装单个决策逻辑。

    功能:
        - 定义决策的基本属性和方法
    """

class L0RuleEngine:
    """
    L0规则引擎类，用于管理多个决策规则并执行。

    功能:
        - 加载和管理规则
        - 执行规则并返回决策结果
    """
# endregion

# region 模块导出
__all__ = ["L0Decision", "L0RuleEngine"]
# endregion
