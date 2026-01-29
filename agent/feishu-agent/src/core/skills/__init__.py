"""
Skills package for Feishu Agent.

Exports all skill classes for registration.
"""

from src.core.skills.query import QuerySkill
from src.core.skills.summary import SummarySkill
from src.core.skills.reminder import ReminderSkill
from src.core.skills.chitchat import ChitchatSkill

__all__ = [
    "QuerySkill",
    "SummarySkill",
    "ReminderSkill",
    "ChitchatSkill",
]
