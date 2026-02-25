"""
描述: 核心数据类型定义
主要功能:
    - SkillContext: 技能执行上下文
    - SkillResult: 技能执行结果
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SkillExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    ERROR = "error"


# region 技能上下文
@dataclass
class SkillContext:
    """
    技能执行上下文
    
    功能:
        - 传递 Query 和 UserID
        - 存储链式调用间的结果 (last_result)
        - 传递扩展信息 (extra)
    """

    query: str
    user_id: str = ""
    last_result: dict[str, Any] | None = None
    last_skill: str | None = None
    hop_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def with_result(self, skill_name: str, result: dict[str, Any]) -> "SkillContext":
        """生成包含本次执行结果的新上下文"""
        return SkillContext(
            query=self.query,
            user_id=self.user_id,
            last_result=result,
            last_skill=skill_name,
            hop_count=self.hop_count + 1,
            extra=self.extra.copy(),
        )
# endregion


# region 技能结果
@dataclass
class SkillResult:
    """
    技能执行结果
    
    属性:
        success: 是否成功
        skill_name: 执行技能名
        data: 结构化数据
        reply_text: 回复文本
        reply_card: 飞书卡片数据 (可选)
    """

    success: bool
    skill_name: str
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    reply_type: str = "text"  # text / card
    reply_text: str = ""
    reply_card: dict[str, Any] | None = None

    def to_reply(self) -> dict[str, Any]:
        """转换为标准回复字典"""
        base = {
            "type": self.reply_type,
            "text": self.reply_text or self.message,
        }
        if self.reply_card:
            return {
                **base,
                "card": self.reply_card,
            }
        return base
# endregion
