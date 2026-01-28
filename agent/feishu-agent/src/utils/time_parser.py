"""
Time range parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional


@dataclass
class TimeRange:
    date_from: str
    date_to: str


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


def parse_time_range(text: str) -> Optional[TimeRange]:
    today = date.today()
    if "下周" in text:
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
            if key in text:
                days_until_next_week = 7 - today.weekday()
                target = today + timedelta(days=days_until_next_week + weekday)
                return TimeRange(_format_day(target), _format_day(target))
    if "今天" in text:
        return TimeRange(_format_day(today), _format_day(today))
    if "明天" in text:
        target = today + timedelta(days=1)
        return TimeRange(_format_day(target), _format_day(target))
    if "本周" in text or "这周" in text:
        return _week_range(today)
    if "下周" in text:
        target = today + timedelta(days=7)
        return _week_range(target)
    if "这个月" in text or "本月" in text:
        return _month_range(today)
    return None
