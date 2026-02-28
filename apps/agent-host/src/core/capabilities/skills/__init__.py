"""
描述: Skills package for Feishu Agent.
主要功能:
    - 导出所有技能类以供注册
"""

from __future__ import annotations

from src.core.capabilities.skills.base.base import BaseSkill
from src.core.capabilities.skills.implementations.query import QuerySkill
from src.core.capabilities.skills.implementations.summary import SummarySkill
from src.core.capabilities.skills.reminders.reminder import ReminderSkill
from src.core.capabilities.skills.implementations.chitchat import ChitchatSkill
from src.core.capabilities.skills.implementations.create import CreateSkill
from src.core.capabilities.skills.implementations.update import UpdateSkill
from src.core.capabilities.skills.implementations.delete import DeleteSkill
from src.core.capabilities.skills.implementations.qa import KnowledgeQASkill

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
    "KnowledgeQASkill",
]
# endregion
