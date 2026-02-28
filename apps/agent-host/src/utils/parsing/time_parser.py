"""
描述: 时间范围解析器。
主要功能:
    - 解析自然语言中的时间范围
    - 提取日期和时间信息
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from typing import Optional


@dataclass
class TimeRange:
    """
    表示一个时间范围。

    属性:
        - date_from: 起始日期
        - date_to: 结束日期
        - time_from: 起始时间（可选）
        - time_to: 结束时间（可选）
    """
    date_from: str
    date_to: str
    time_from: str | None = None
    time_to: str | None = None


def _format_day(value: date) -> str:
    """
    将日期格式化为 ISO 格式的字符串。

    功能:
        - 接受一个 date 对象
        - 返回 ISO 格式的日期字符串
    """
    return value.isoformat()


def _week_range(target: date) -> TimeRange:
    """
    计算给定日期所在周的范围。

    功能:
        - 找到目标日期所在周的周一
        - 计算该周的周日
        - 返回一个 TimeRange 对象
    """
    start = target - timedelta(days=target.weekday())
    end = start + timedelta(days=6)
    return TimeRange(_format_day(start), _format_day(end))


def _month_range(target: date) -> TimeRange:
    """
    计算给定日期所在月的范围。

    功能:
        - 找到目标日期所在月的第一天
        - 计算该月的最后一天
        - 返回一个 TimeRange 对象
    """
    start = target.replace(day=1)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    end = next_month - timedelta(days=1)
    return TimeRange(_format_day(start), _format_day(end))


def _month_range_by_year_month(year: int, month: int) -> Optional[TimeRange]:
    """
    根据年份和月份计算该月的范围。

    功能:
        - 验证年份和月份的有效性
        - 计算该月的第一天和最后一天
        - 返回一个 TimeRange 对象或 None
    """
    start = _safe_date(year, month, 1)
    if start is None:
        return None
    if month == 12:
        next_month_start = date(year + 1, 1, 1)
    else:
        next_month_start = date(year, month + 1, 1)
    end = next_month_start - timedelta(days=1)
    return TimeRange(_format_day(start), _format_day(end))


def _format_hm(hour: int, minute: int) -> str:
    """
    将小时和分钟格式化为 HH:MM 格式的字符串。

    功能:
        - 接受小时和分钟
        - 返回格式化后的字符串
    """
    return f"{hour:02d}:{minute:02d}"


def _adjust_hour(hour: int, period: str) -> int:
    """
    根据时间段调整小时。

    功能:
        - 根据时间段（如“下午”、“晚上”）调整小时
        - 返回调整后的小时
    """
    p = period or ""
    if p in {"下午", "傍晚", "晚上", "今晚", "明晚"} and 1 <= hour <= 11:
        return hour + 12
    if p in {"中午"}:
        if hour == 12:
            return 12
        if 1 <= hour <= 11:
            return hour + 12
    if p in {"凌晨"} and hour == 12:
        return 0
    return hour


def _extract_relative_day(text: str, today: date) -> date | None:
    """
    从文本中提取相对日期。

    功能:
        - 支持“今天”、“明天”、“后天”等相对日期
        - 返回对应的日期对象或 None
    """
    week_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    m = re.search(r"(下周|本周|这周|周)([一二三四五六日天])", text)
    if m:
        prefix = m.group(1)
        weekday = week_map[m.group(2)]
        if prefix == "下周":
            next_week_start = today + timedelta(days=7 - today.weekday())
            return next_week_start + timedelta(days=weekday)
        this_week_start = today - timedelta(days=today.weekday())
        target = this_week_start + timedelta(days=weekday)
        if prefix == "周" and target < today:
            target = target + timedelta(days=7)
        return target

    if "大后天" in text:
        return today + timedelta(days=3)
    if "后天" in text:
        return today + timedelta(days=2)
    if any(token in text for token in ["明天", "明早", "明晚"]):
        return today + timedelta(days=1)
    if any(token in text for token in ["今天", "今早", "今晚"]):
        return today
    return None


def _parse_day_count(token: str) -> int | None:
    """
    解析天数字符串为整数。

    功能:
        - 支持中文数字和阿拉伯数字
        - 返回解析后的天数或 None
    """
    raw = str(token or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)

    mapping = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if raw in mapping:
        return mapping[raw]
    if raw == "十":
        return 10
    if len(raw) == 2 and raw.startswith("十") and raw[1] in mapping:
        return 10 + mapping[raw[1]]
    if len(raw) == 2 and raw.endswith("十") and raw[0] in mapping:
        return mapping[raw[0]] * 10
    return None


def _extract_future_days_range(text: str, today: date) -> TimeRange | None:
    """
    从文本中提取未来几天的范围。

    功能:
        - 支持“未来几天”、“接下来几天”等表达
        - 返回对应的 TimeRange 对象或 None
    """
    matched = re.search(r"(?:未来|接下来)\s*([一二两三四五六七八九十\d]{1,3})\s*天", text)
    if not matched:
        return None
    days = _parse_day_count(matched.group(1))
    if days is None or days <= 0:
        return None
    return TimeRange(_format_day(today), _format_day(today + timedelta(days=days)))


def _extract_after_days(text: str, today: date) -> date | None:
    """
    从文本中提取几天后的日期。

    功能:
        - 支持“过几天”、“再过几天”等表达
        - 返回对应的日期对象或 None
    """
    patterns = (
        r"(?:过|再过|还有)\s*([一二两三四五六七八九十\d]{1,3})\s*天",
        r"([一二两三四五六七八九十\d]{1,3})\s*天后",
    )
    for pattern in patterns:
        matched = re.search(pattern, text)
        if not matched:
            continue
        days = _parse_day_count(matched.group(1))
        if days is None or days <= 0:
            continue
        return today + timedelta(days=days)
    return None


def _extract_month_range(text: str, today: date) -> TimeRange | None:
    """
    从文本中提取月份范围。

    功能:
        - 支持“上个月”、“下个月”、“这个月”等表达
        - 返回对应的 TimeRange 对象或 None
    """
    if "上个月" in text:
        prev_month_end = today.replace(day=1) - timedelta(days=1)
        return _month_range(prev_month_end)
    if "下个月" in text:
        next_month_anchor = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        return _month_range(next_month_anchor)
    if "这个月" in text or "本月" in text or "这月" in text:
        return _month_range(today)

    year_month = re.search(r"(?<!\d)(\d{4})\s*年\s*(\d{1,2})\s*月(?:份)?(?!\d)", text)
    if year_month:
        year = int(year_month.group(1))
        month = int(year_month.group(2))
        if 1 <= month <= 12:
            return _month_range_by_year_month(year, month)

    month_only = re.search(r"(?<!\d)(\d{1,2})\s*月(?:份)?(?!\d)", text)
    if not month_only:
        return None
    month = int(month_only.group(1))
    if not 1 <= month <= 12:
        return None
    year = today.year
    if "明年" in text:
        year += 1
    elif "去年" in text:
        year -= 1
    return _month_range_by_year_month(year, month)


def _extract_time_window(text: str) -> tuple[str | None, str | None]:
    """
    从文本中提取时间段。

    功能:
        - 支持具体时刻和时间段的提取
        - 返回时间段的起始和结束时间
    """
    # 先识别具体时刻
    m = re.search(r"(凌晨|早上|上午|中午|下午|傍晚|晚上|今晚|明晚|今早|明早)?\s*(\d{1,2})[:：](\d{1,2})", text)
    if m:
        period = m.group(1) or ""
        hour = _adjust_hour(int(m.group(2)), period)
        minute = int(m.group(3))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            hm = _format_hm(hour, minute)
            return hm, hm

    m = re.search(r"(凌晨|早上|上午|中午|下午|傍晚|晚上|今晚|明晚|今早|明早)?\s*(\d{1,2})点(?:\s*(半|\d{1,2}分?))?", text)
    if m:
        period = m.group(1) or ""
        hour = _adjust_hour(int(m.group(2)), period)
        minute_token = m.group(3) or ""
        if minute_token == "半":
            minute = 30
            hm = _format_hm(hour, minute)
            return hm, hm
        if minute_token:
            minute = int(re.sub(r"分", "", minute_token))
            if 0 <= minute <= 59:
                hm = _format_hm(hour, minute)
                return hm, hm
        if 0 <= hour <= 23:
            return _format_hm(hour, 0), _format_hm(hour, 59)

    # 再识别时间段
    if "凌晨" in text:
        return "00:00", "05:59"
    if any(token in text for token in ["明早", "今早", "早上", "早晨", "上午"]):
        return "06:00", "11:59"
    if "中午" in text:
        return "11:00", "13:59"
    if "下午" in text:
        return "13:00", "17:59"
    if "傍晚" in text:
        return "17:00", "18:59"
    if any(token in text for token in ["晚上", "今晚", "夜里", "夜间", "明晚"]):
        return "18:00", "23:59"
    return None, None


def _normalize_text(text: str) -> str:
    """
    规范化文本中的特殊字符。

    功能:
        - 替换常见的全角字符为半角字符
        - 返回规范化后的文本
    """
    normalized = str(text or "")
    replacements = {
        "／": "/",
        "－": "-",
        "—": "-",
        "–": "-",
        "．": ".",
        "：": ":",
        "～": "~",
    }
    for src, dst in replacements.items():
        normalized = normalized.replace(src, dst)
    return normalized.strip()


def _safe_date(year: int, month: int, day: int) -> Optional[date]:
    """
    安全地创建一个日期对象。

    功能:
        - 尝试创建日期对象
        - 如果失败则返回 None
    """
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _extract_explicit_dates(text: str, today: date) -> list[date]:
    """
    提取文本中的显式日期。

    功能:
        - 支持多种日期格式（如 YYYY/MM/DD, YYYY-MM-DD, YYYY年M月D日, M/D, M月D日）
        - 返回提取的日期列表
    """
    ymd_pattern = re.compile(r"(?<!\d)(\d{4})\s*(?:年|[\-/\.])\s*(\d{1,2})\s*(?:月|[\-/\.])\s*(\d{1,2})\s*(?:日|号)?")
    md_pattern = re.compile(r"(?<!\d)(\d{1,2})\s*(?:月|[\-/\.])\s*(\d{1,2})\s*(?:日|号)?(?!\d)")

    matches: list[tuple[int, date]] = []
    occupied: list[tuple[int, int]] = []

    for m in ymd_pattern.finditer(text):
        dt = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if dt:
            matches.append((m.start(), dt))
            occupied.append((m.start(), m.end()))

    masked_chars = list(text)
    for start, end in occupied:
        for idx in range(start, end):
            masked_chars[idx] = " "
    masked_text = "".join(masked_chars)

    for m in md_pattern.finditer(masked_text):
        dt = _safe_date(today.year, int(m.group(1)), int(m.group(2)))
        if dt:
            matches.append((m.start(), dt))

    matches.sort(key=lambda item: item[0])
    return [item[1] for item in matches]


def parse_time_range(text: str) -> Optional[TimeRange]:
    """
    解析时间范围。

    功能:
        - 处理各种时间范围的表达
        - 返回解析后的时间范围对象或 None
    """
    today = date.today()
    normalized = _normalize_text(text)
    time_from, time_to = _extract_time_window(normalized)

    future_days_range = _extract_future_days_range(normalized, today)
    if future_days_range is not None:
        future_days_range.time_from = time_from
        future_days_range.time_to = time_to
        return future_days_range

    explicit_dates = _extract_explicit_dates(normalized, today)
    if explicit_dates:
        has_range_hint = bool(re.search(r"(到|至|~|之间|起?至)", normalized))
        if has_range_hint and len(explicit_dates) >= 2:
            first = explicit_dates[0]
            second = explicit_dates[1]
            start, end = (first, second) if first <= second else (second, first)
            return TimeRange(_format_day(start), _format_day(end), time_from=time_from, time_to=time_to)
        target = explicit_dates[0]
        return TimeRange(_format_day(target), _format_day(target), time_from=time_from, time_to=time_to)

    month_range = _extract_month_range(normalized, today)
    if month_range is not None:
        month_range.time_from = time_from
        month_range.time_to = time_to
        return month_range

    after_days = _extract_after_days(normalized, today)
    if after_days is not None:
        return TimeRange(_format_day(after_days), _format_day(after_days), time_from=time_from, time_to=time_to)

    relative_day = _extract_relative_day(normalized, today)
    if relative_day is not None:
        return TimeRange(_format_day(relative_day), _format_day(relative_day), time_from=time_from, time_to=time_to)

    if "下周" in normalized:
        week_map = {
            "下周一": 0,
            "下周二": 1,
            "下周三": 2,
            "下周四": 3,
            "下周五": 4,
            "下周六": 5,
            "下周日": 6,
        }
        for key, weekday in week_map.items():
            if key in normalized:
                days_until_next_week = 7 - today.weekday()
                target = today + timedelta(days=days_until_next_week + weekday)
                return TimeRange(_format_day(target), _format_day(target), time_from=time_from, time_to=time_to)
    if "今天" in normalized:
        return TimeRange(_format_day(today), _format_day(today), time_from=time_from, time_to=time_to)
    if "明天" in normalized:
        target = today + timedelta(days=1)
        return TimeRange(_format_day(target), _format_day(target), time_from=time_from, time_to=time_to)
    if "本周" in normalized or "这周" in normalized:
        base = _week_range(today)
        base.time_from = time_from
        base.time_to = time_to
        return base
    if "下周" in normalized:
        target = today + timedelta(days=7)
        base = _week_range(target)
        base.time_from = time_from
        base.time_to = time_to
        return base

    # 仅时间段时默认今天
    if time_from or time_to:
        return TimeRange(_format_day(today), _format_day(today), time_from=time_from, time_to=time_to)
    return None
