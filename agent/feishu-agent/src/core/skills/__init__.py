"""
Skills package for Feishu Agent.

Exports all skill classes for registration.
"""

from src.core.skills.base import BaseSkill
from src.core.skills.query import QuerySkill
from src.core.skills.summary import SummarySkill
from src.core.skills.reminder import ReminderSkill
from src.core.skills.chitchat import ChitchatSkill
from src.core.skills.create import CreateSkill

__all__ = [
    "QuerySkill",
    "CreateSkill",
    "SummarySkill",
    "ReminderSkill",
    "ChitchatSkill",
    "BaseSkill",
]
