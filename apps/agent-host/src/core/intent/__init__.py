"""
描述: 提供意图解析功能的模块
主要功能:
    - 提供IntentParser类用于解析用户意图
    - 提供IntentResult类用于存储解析结果
    - 提供SkillMatch类用于匹配技能
    - 提供load_skills_config函数用于加载技能配置
"""

from src.core.intent.parser import IntentParser, IntentResult, SkillMatch, load_skills_config

__all__ = [
    "IntentParser",
    "IntentResult",
    "SkillMatch",
    "load_skills_config",
]

# region 类定义
class IntentParser:
    """
    意图解析器类

    功能:
        - 解析用户输入的文本以识别其意图
        - 返回解析结果
    """
    pass

class IntentResult:
    """
    意图解析结果类

    功能:
        - 存储意图解析的结果信息
        - 提供访问解析结果的接口
    """
    pass

class SkillMatch:
    """
    技能匹配类

    功能:
        - 匹配用户意图与相应的技能
        - 返回匹配结果
    """
    pass
# endregion

# region 函数定义
def load_skills_config():
    """
    加载技能配置函数

    功能:
        - 从配置文件中加载技能信息
        - 返回加载的技能配置
    """
    pass
# endregion
