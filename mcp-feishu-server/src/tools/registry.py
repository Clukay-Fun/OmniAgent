"""
Tool registry for MCP server.
"""

from __future__ import annotations

from typing import Any, Type

from src.tools.base import BaseTool


class ToolRegistry:
    _tools: dict[str, Type[BaseTool]] = {}

    @classmethod
    def register(cls, tool_cls: Type[BaseTool]) -> Type[BaseTool]:
        cls._tools[tool_cls.name] = tool_cls
        return tool_cls

    @classmethod
    def get(cls, name: str) -> Type[BaseTool] | None:
        return cls._tools.get(name)

    @classmethod
    def list_tools(cls) -> list[dict[str, Any]]:
        return [
            {
                "name": tool_cls.name,
                "description": tool_cls.description,
            }
            for tool_cls in cls._tools.values()
        ]
