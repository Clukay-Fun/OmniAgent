"""
Skills package for Feishu Agent.

Exports all skill classes for registration.
"""

from src.agent.skills.query import QuerySkill
from src.agent.skills.summary import SummarySkill
from src.agent.skills.reminder import ReminderSkill
from src.agent.skills.chitchat import ChitchatSkill

__all__ = [
    "QuerySkill",
    "SummarySkill",
    "ReminderSkill",
    "ChitchatSkill",
]
