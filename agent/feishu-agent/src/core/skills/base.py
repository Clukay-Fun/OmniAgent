"""Base skill definitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.types import SkillContext, SkillResult


class BaseSkill:
    name: str = "BaseSkill"
    description: str = ""
    keywords: list[str] = []

    def match(self, query: str, context: "SkillContext") -> float:
        return 0.0

    async def run(self, query: str, context: "SkillContext") -> "SkillResult":
        raise NotImplementedError("Subclass must implement run()")

    async def execute(self, context: "SkillContext") -> "SkillResult":
        return await self.run(context.query, context)
