"""
描述: 该模块负责将渲染后的响应通过飞书发送出去
主要功能:
    - 初始化飞书发送器，配置发送方法和格式化器
    - 发送格式化后的消息到飞书
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from src.adapters.channels.feishu.formatter import FeishuFormatter
from src.core.response.models import RenderedResponse


class FeishuSender:
    """
    初始化飞书发送器

    功能:
        - 接收一个发送方法和一个格式化器实例
        - 将这些实例存储为私有属性
    """
    def __init__(
        self,
        send_callable: Callable[[Dict[str, Any]], Any],
        formatter: FeishuFormatter,
    ) -> None:
        self._send_callable = send_callable
        self._formatter = formatter

    # region 发送消息逻辑
    """
    发送格式化后的消息到飞书

    功能:
        - 使用格式化器将渲染后的响应格式化为飞书所需格式
        - 调用发送方法将格式化后的消息发送出去
    """
    def send(self, rendered: RenderedResponse) -> Any:
        payload = self._formatter.format(rendered)
        return self._send_callable(payload)
    # endregion
