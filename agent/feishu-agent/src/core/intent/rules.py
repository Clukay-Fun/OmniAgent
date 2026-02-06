"""
描述: 意图识别规则库 (Regex Patterns)
主要功能:
    - 编译正则模式
    - 提供特定领域 (如日期查询) 的硬规则匹配逻辑
"""

from __future__ import annotations

import re
from typing import Iterable


# region 正则辅助与模式定义
def compile_trigger_patterns(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    """预编译正则模式列表"""
    return [re.compile(pattern) for pattern in patterns if pattern]


# 日期类查询正则
DATE_QUERY_PATTERNS = [
    r"(今天|明天|后天|本周|下周|这周).*(有|什么|哪些).*(庭|案|开庭|案件)",
    r"(今天|明天|后天).*(开庭|庭审)",
    r"\d{1,2}月\d{1,2}[日号].*(有|什么|庭|案)",
    r"\d{4}年\d{1,2}月\d{1,2}[日号]",
    r"(查|找|搜|看).*(今天|明天|后天|本周|这周|下周)",
    r"(有没有|有哪些).*(今天|明天|本周|这周|下周).*(庭|案)",
]


def match_date_query(query: str) -> float:
    """
    基于规则匹配日期查询意图
    
    返回:
        0.0 ~ 1.0 置信度 (匹配成功返回 0.95)
    """
    # 排除提醒类意图 (避免冲突)
    if any(trigger in query for trigger in ("提醒", "记得", "别忘了")):
        return 0.0
    for pattern in DATE_QUERY_PATTERNS:
        if re.search(pattern, query):
            return 0.95
    return 0.0
# endregion
