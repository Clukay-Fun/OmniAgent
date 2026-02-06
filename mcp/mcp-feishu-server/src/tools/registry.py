"""
描述: MCP 工具注册中心
主要功能:
    - 统一管理所有 MCP 工具的注册
    - 提供工具查找与元数据列表功能
"""

from __future__ import annotations

import logging
from typing import Any, Type

from src.tools.base import BaseTool


# region 工具注册中心
class ToolRegistry:
    """工具注册中心 (单例模式)"""
    _tools: dict[str, Type[BaseTool]] = {}
    _logger = logging.getLogger(__name__)

    @classmethod
    def register(cls, tool_cls: Type[BaseTool]) -> Type[BaseTool]:
        tool_name = getattr(tool_cls, "name", "")
        if not tool_name:
            cls._logger.warning(
                "Tool %s has no 'name' attribute, skipping registration",
                tool_cls.__name__,
            )
            return tool_cls
        if tool_name in cls._tools:
            cls._logger.warning("Tool %s already registered, overwriting", tool_name)
        cls._tools[tool_name] = tool_cls
        return tool_cls

    @classmethod
    def get(cls, name: str) -> Type[BaseTool] | None:
        return cls._tools.get(name)

    @classmethod
    def list_tools(cls) -> list[dict[str, Any]]:
        """获取所有已注册工具的元数据"""
        return [
            {
                "name": tool_cls.name,
                "description": tool_cls.description,
            }
            for tool_cls in cls._tools.values()
        ]
# endregion
