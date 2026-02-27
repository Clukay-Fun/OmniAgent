"""
描述: Skills package for Feishu Agent.
主要功能:
    - 导出所有技能类以供注册
"""

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

# region 技能类定义
class BaseSkill:
    """
    基础技能类

    功能:
        - 提供所有技能类的通用接口和方法
    """
    pass

class QuerySkill(BaseSkill):
    """
    查询技能类

    功能:
        - 实现数据查询功能
    """
    pass

class SummarySkill(BaseSkill):
    """
    概要技能类

    功能:
        - 实现数据概要生成功能
    """
    pass

class ReminderSkill(BaseSkill):
    """
    提醒技能类

    功能:
        - 实现提醒功能
    """
    pass

class ChitchatSkill(BaseSkill):
    """
    闲聊技能类

    功能:
        - 实现与用户的闲聊功能
    """
    pass

class CreateSkill(BaseSkill):
    """
    创建技能类

    功能:
        - 实现数据创建功能
    """
    pass

class UpdateSkill(BaseSkill):
    """
    更新技能类

    功能:
        - 实现数据更新功能
    """
    pass

class DeleteSkill(BaseSkill):
    """
    删除技能类

    功能:
        - 实现数据删除功能
    """
    pass
# endregion
