"""
描述: 该模块定义了用于语义槽提取的枚举和数据类。
主要功能:
    - 定义语义槽的键枚举
    - 定义语义槽提取结果的数据类
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SemanticSlotKey(str, Enum):
    """
    语义槽的键枚举

    功能:
        - 定义了各个语义槽的键，如案件标识符、当事人A、当事人B等
    """
    CASE_IDENTIFIER = "case_identifier"
    PARTY_A = "party_a"
    PARTY_B = "party_b"
    COURT = "court"
    STAGE = "stage"
    OWNER = "owner"
    STATUS = "status"
    HEARING_DATE = "hearing_date"


# region 语义槽提取结果的数据类
@dataclass
class SemanticSlotExtraction:
    """
    语义槽提取结果的数据类

    功能:
        - 存储提取的语义槽及其值
        - 存储缺失的必需语义槽
        - 存储提取结果的置信度
    """
    slots: dict[SemanticSlotKey, str] = field(default_factory=dict)
    missing_required: list[SemanticSlotKey] = field(default_factory=list)
    confidence: Optional[float] = None
# endregion
