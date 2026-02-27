"""
描述: 提供飞书消息卡片构建的适配器
主要功能:
    - 定义飞书消息卡片构建错误的异常类
    - 提供飞书消息卡片格式化的工具类
"""

from src.adapters.channels.feishu.formatter import CardBuildError, FeishuFormatter

__all__ = ["CardBuildError", "FeishuFormatter"]

# region 异常类定义
class CardBuildError(Exception):
    """
    飞书消息卡片构建错误的异常类

    功能:
        - 继承自 Python 内置的 Exception 类
        - 用于在飞书消息卡片构建过程中抛出异常
    """
    pass
# endregion

# region 工具类定义
class FeishuFormatter:
    """
    飞书消息卡片格式化的工具类

    功能:
        - 提供构建飞书消息卡片的方法
        - 处理消息内容并格式化为飞书支持的卡片结构
    """
    pass
# endregion
