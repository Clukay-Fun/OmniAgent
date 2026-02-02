"""Intent rules helpers."""

from __future__ import annotations

import re
from typing import Iterable


def compile_trigger_patterns(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    return [re.compile(pattern) for pattern in patterns if pattern]


DATE_QUERY_PATTERNS = [
    r"(今天|明天|后天|本周|下周|这周).*(有|什么|哪些).*(庭|案|开庭|案件)",
    r"(今天|明天|后天).*(开庭|庭审)",
    r"\d{1,2}月\d{1,2}[日号].*(有|什么|庭|案)",
    r"\d{4}年\d{1,2}月\d{1,2}[日号]",
    r"(查|找|搜|看).*(今天|明天|后天|本周|这周|下周)",
    r"(有没有|有哪些).*(今天|明天|本周|这周|下周).*(庭|案)",
]


def match_date_query(query: str) -> float:
    for pattern in DATE_QUERY_PATTERNS:
        if re.search(pattern, query):
            return 0.95
    return 0.0
