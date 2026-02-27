"""
描述: L1 Planner 模块的核心职责是提供路径规划功能。
主要功能:
    - 初始化路径规划引擎
    - 生成路径规划输出
"""

from src.core.planner.engine import PlannerEngine
from src.core.planner.schema import PlannerOutput

__all__ = ["PlannerEngine", "PlannerOutput"]

# region 类定义
class PlannerEngine:
    """
    路径规划引擎的核心类。

    功能:
        - 初始化路径规划引擎
        - 执行路径规划算法
    """
    pass

class PlannerOutput:
    """
    路径规划输出的数据结构。

    功能:
        - 存储路径规划结果
        - 提供路径规划结果的访问接口
    """
    pass
# endregion

# region 模块导出
__all__ = ["PlannerEngine", "PlannerOutput"]
# endregion
