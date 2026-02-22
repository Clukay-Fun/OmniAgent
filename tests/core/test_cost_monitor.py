from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.cost_monitor import CostMonitor


def test_hourly_and_daily_threshold_crossing_only_triggers_once_per_window() -> None:
    monitor = CostMonitor(hourly_threshold=5.0, daily_threshold=10.0, circuit_breaker_enabled=False)
    base = datetime(2026, 2, 22, 9, 0, 0)

    assert monitor.record_cost(skill="QuerySkill", cost=3.0, ts=base) == []
    assert monitor.record_cost(skill="QuerySkill", cost=2.5, ts=base + timedelta(minutes=10)) == ["hourly"]
    assert monitor.record_cost(skill="QuerySkill", cost=1.0, ts=base + timedelta(minutes=20)) == []
    assert monitor.record_cost(skill="SummarySkill", cost=4.0, ts=base + timedelta(hours=2)) == ["daily"]


def test_top3_skill_extraction_by_window_cost() -> None:
    monitor = CostMonitor(hourly_threshold=99.0, daily_threshold=999.0, circuit_breaker_enabled=False)
    base = datetime(2026, 2, 22, 10, 0, 0)

    monitor.record_cost(skill="A", cost=1.0, ts=base)
    monitor.record_cost(skill="B", cost=3.0, ts=base + timedelta(minutes=1))
    monitor.record_cost(skill="C", cost=2.5, ts=base + timedelta(minutes=2))
    monitor.record_cost(skill="D", cost=9.0, ts=base - timedelta(days=1))

    top3 = monitor.top_skills(window="daily", now=base + timedelta(minutes=3))
    assert top3 == [("B", 3.0), ("C", 2.5), ("A", 1.0)]


def test_circuit_breaker_blocks_calls_when_enabled_and_daily_exceeded() -> None:
    monitor = CostMonitor(hourly_threshold=99.0, daily_threshold=5.0, circuit_breaker_enabled=True)
    base = datetime(2026, 2, 22, 12, 0, 0)
    monitor.record_cost(skill="QuerySkill", cost=6.0, ts=base)

    allowed, guidance = monitor.check_call_allowed("llm", now=base + timedelta(minutes=1))
    assert allowed is False
    assert "预算" in guidance


def test_circuit_breaker_allows_calls_when_disabled_or_next_day() -> None:
    base = datetime(2026, 2, 22, 12, 0, 0)

    disabled_monitor = CostMonitor(hourly_threshold=1.0, daily_threshold=1.0, circuit_breaker_enabled=False)
    disabled_monitor.record_cost(skill="QuerySkill", cost=10.0, ts=base)
    allowed_disabled, _ = disabled_monitor.check_call_allowed("llm", now=base)
    assert allowed_disabled is True

    enabled_monitor = CostMonitor(hourly_threshold=99.0, daily_threshold=5.0, circuit_breaker_enabled=True)
    enabled_monitor.record_cost(skill="QuerySkill", cost=6.0, ts=base)
    allowed_today, _ = enabled_monitor.check_call_allowed("asr", now=base + timedelta(minutes=1))
    allowed_tomorrow, _ = enabled_monitor.check_call_allowed("asr", now=base + timedelta(days=1))
    assert allowed_today is False
    assert allowed_tomorrow is True
