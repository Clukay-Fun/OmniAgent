"""Shared core types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillContext:
    """技能执行上下文，用于链式调用间传递数据"""

    query: str
    user_id: str = ""
    last_result: dict[str, Any] | None = None
    last_skill: str | None = None
    hop_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def with_result(self, skill_name: str, result: dict[str, Any]) -> "SkillContext":
        return SkillContext(
            query=self.query,
            user_id=self.user_id,
            last_result=result,
            last_skill=skill_name,
            hop_count=self.hop_count + 1,
            extra=self.extra.copy(),
        )


@dataclass
class SkillResult:
    """技能执行结果"""

    success: bool
    skill_name: str
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    reply_type: str = "text"  # text / card
    reply_text: str = ""
    reply_card: dict[str, Any] | None = None

    def to_reply(self) -> dict[str, Any]:
        result = {
            "type": self.reply_type,
            "text": self.reply_text or self.message,
        }
        if self.reply_card:
            result["card"] = self.reply_card
        return result
