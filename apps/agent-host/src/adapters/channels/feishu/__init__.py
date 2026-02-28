"""
描述: 提供飞书消息卡片构建的适配器
主要功能:
    - 定义飞书消息卡片构建错误的异常类
    - 提供飞书消息卡片格式化的工具类
"""

from __future__ import annotations

from src.adapters.channels.feishu.formatter import CardBuildError, FeishuFormatter

__all__ = ["CardBuildError", "FeishuFormatter"]
