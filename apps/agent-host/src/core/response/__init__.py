"""
描述: 提供响应模型和渲染器的核心模块
主要功能:
    - 定义响应块模型
    - 定义渲染后的响应模型
    - 提供响应渲染器
"""

from src.core.response.models import Block, RenderedResponse
from src.core.response.renderer import ResponseRenderer

__all__ = ["Block", "RenderedResponse", "ResponseRenderer"]

# region 模型定义
class Block:
    """
    响应块模型

    功能:
        - 定义响应块的基本结构
    """
    pass

class RenderedResponse:
    """
    渲染后的响应模型

    功能:
        - 定义渲染后响应的基本结构
    """
    pass
# endregion

# region 渲染器定义
class ResponseRenderer:
    """
    响应渲染器

    功能:
        - 提供渲染响应的逻辑
    """
    pass
# endregion
