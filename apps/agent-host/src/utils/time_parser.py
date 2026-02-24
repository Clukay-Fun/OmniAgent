"""
Time range parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from typing import Optional


@dataclass
class TimeRange:
    date_from: str
    date_to: str
    time_from: str | None = None
    time_to: str | None = None


def _format_day(value: date) -> str:
    return value.isoformat()


def _week_range(target: date) -> TimeRange:
    start = target - timedelta(days=target.weekday())
    end = start + timedelta(days=6)
    return TimeRange(_format_day(start), _format_day(end))


def _month_range(target: date) -> TimeRange:
    start = target.replace(day=1)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    end = next_month - timedelta(days=1)
    return TimeRange(_format_day(start), _format_day(end))


def _format_hm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def _adjust_hour(hour: int, period: str) -> int:
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


def _extract_time_window(text: str) -> tuple[str | None, str | None]:
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
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _extract_explicit_dates(text: str, today: date) -> list[date]:
    """提取文本中的显式日期（支持 YYYY/MM/DD, YYYY-MM-DD, YYYY年M月D日, M/D, M月D日）。"""
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
    today = date.today()
    normalized = _normalize_text(text)
    time_from, time_to = _extract_time_window(normalized)

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
    if "这个月" in normalized or "本月" in normalized:
        base = _month_range(today)
        base.time_from = time_from
        base.time_to = time_to
        return base
    if "下个月" in normalized:
        next_month_anchor = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        base = _month_range(next_month_anchor)
        base.time_from = time_from
        base.time_to = time_to
        return base

    # 仅时间段时默认今天
    if time_from or time_to:
        return TimeRange(_format_day(today), _format_day(today), time_from=time_from, time_to=time_to)
    return None
