"""
描述: Skills package for Feishu Agent.
主要功能:
    - 导出所有技能类以供注册
"""

from __future__ import annotations

from src.core.skills.base import BaseSkill
from src.core.skills.query import QuerySkill
from src.core.skills.summary import SummarySkill
from src.core.skills.reminder import ReminderSkill
from src.core.skills.chitchat import ChitchatSkill
from src.core.skills.create import CreateSkill
from src.core.skills.update import UpdateSkill
from src.core.skills.delete import DeleteSkill

# region 技能类导出
__all__ = [
    "BaseSkill",
    "QuerySkill",
    "SummarySkill",
    "ReminderSkill",
    "ChitchatSkill",
    "CreateSkill",
    "UpdateSkill",
    "DeleteSkill",
]
# endregion
