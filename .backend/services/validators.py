"""
描述: 服务层校验工具
主要功能:
    - 日期解析
    - 逗号标准化
    - 数值非负校验
依赖: 标准库
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日")

# ============================================
# region normalize_commas
# ============================================
def normalize_commas(value: str | None) -> str | None:
    """
    统一中文分隔符为英文逗号

    参数:
        value: 原始字符串
    返回:
        规范化后的字符串
    """

    if value is None:
        return None
    return value.replace("，", ",").replace("、", ",")
# endregion
# ============================================

# ============================================
# region ensure_non_negative
# ============================================
def ensure_non_negative(value: Decimal, field_name: str) -> None:
    """
    校验非负数值

    参数:
        value: 数值
        field_name: 字段名
    返回:
        None
    """

    if value < 0:
        raise ValueError(f"{field_name} must be >= 0")
# endregion
# ============================================

# ============================================
# region parse_sign_date
# ============================================
def parse_sign_date(
    raw: str | None, norm: date | None
) -> tuple[str | None, date | None]:
    """
    解析签署日期

    参数:
        raw: 原始日期字符串
        norm: 标准化日期
    返回:
        原文与标准化日期
    """

    if norm:
        return raw or norm.isoformat(), norm

    if raw:
        for fmt in DATE_FORMATS:
            try:
                return raw, datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        raise ValueError("sign_date_raw format is not supported")

    return None, None
# endregion
# ============================================
