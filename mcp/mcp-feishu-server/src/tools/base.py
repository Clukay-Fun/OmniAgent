"""
描述: MCP 工具基类定义
主要功能:
    - 定义 BaseTool 抽象基类
    - 定义 ToolContext 上下文对象
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from src.config import Settings
from src.feishu.client import FeishuClient


# region 工具上下文与基类
@dataclass
class ToolContext:
    """工具执行上下文 (依赖注入)"""
    settings: Settings
    client: FeishuClient


class BaseTool(ABC):
    """MCP 工具抽象基类"""
    name: str = "base_tool"
    description: str = "Base tool description"

    def __init__(self, context: ToolContext) -> None:
        self.context = context

    @abstractmethod
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        执行工具逻辑

        参数:
            params: 工具参数字典

        返回:
            执行结果字典
        """
        raise NotImplementedError
# endregion
