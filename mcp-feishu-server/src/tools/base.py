"""
MCP tool base classes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from src.config import Settings
from src.feishu.client import FeishuClient


@dataclass
class ToolContext:
    settings: Settings
    client: FeishuClient


class BaseTool(ABC):
    name: str
    description: str

    def __init__(self, context: ToolContext) -> None:
        self.context = context

    @abstractmethod
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError
