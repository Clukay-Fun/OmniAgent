from __future__ import annotations

from src.utils.time_parser import parse_time_range


def test_time_parser_keywords() -> None:
    assert parse_time_range("今天") is not None
    assert parse_time_range("明天") is not None
    assert parse_time_range("本周") is not None
    assert parse_time_range("下周") is not None
    assert parse_time_range("1月28号") is not None
