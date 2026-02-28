"""
描述: 该模块负责监控和管理成本，包括记录成本、触发警报和控制调用。
主要功能:
    - 记录不同技能的成本
    - 根据设定的阈值触发成本警报
    - 控制调用以防止超出预算
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Deque

from src.utils.observability.metrics import record_cost_alert_triggered


logger = logging.getLogger(__name__)


@dataclass
class _CostEntry:
    ts: datetime
    skill: str
    cost: float


@dataclass
class CostMonitorConfig:
    hourly_threshold: float = 5.0
    daily_threshold: float = 50.0
    circuit_breaker_enabled: bool = False


class CostMonitor:
    """
    成本监控类，用于记录和管理成本，并根据设定的阈值触发警报。

    功能:
        - 初始化监控参数
        - 记录成本
        - 检查并触发成本警报
        - 获取指定窗口内的成本最高的技能
        - 检查是否允许新的调用
    """

    def __init__(self, hourly_threshold: float, daily_threshold: float, circuit_breaker_enabled: bool) -> None:
        self._hourly_threshold = max(0.0, float(hourly_threshold))
        self._daily_threshold = max(0.0, float(daily_threshold))
        self._circuit_breaker_enabled = bool(circuit_breaker_enabled)
        self._entries: Deque[_CostEntry] = deque()
        self._alerted_hour_keys: set[str] = set()
        self._alerted_day_keys: set[str] = set()

    def record_cost(self, skill: str, cost: float, ts: datetime | str | None = None) -> list[str]:
        """
        记录成本并检查是否触发警报。

        功能:
            - 标准化时间戳
            - 过滤掉无效的成本记录
            - 添加成本记录到队列
            - 检查并触发小时和日成本警报
        """
        value = max(0.0, float(cost or 0.0))
        entry_ts = self._normalize_ts(ts)
        if value <= 0:
            self._prune(entry_ts)
            return []
        self._entries.append(_CostEntry(ts=entry_ts, skill=str(skill or "unknown"), cost=value))
        self._prune(entry_ts)

        triggered: list[str] = []
        hourly_total = self._window_total("hourly", now=entry_ts)
        if self._hourly_threshold > 0 and hourly_total >= self._hourly_threshold:
            hour_key = entry_ts.strftime("%Y-%m-%dT%H")
            if hour_key not in self._alerted_hour_keys:
                self._alerted_hour_keys.add(hour_key)
                self._log_alert("hourly", hourly_total, self._hourly_threshold, entry_ts)
                record_cost_alert_triggered("hourly")
                triggered.append("hourly")

        daily_total = self._window_total("daily", now=entry_ts)
        if self._daily_threshold > 0 and daily_total >= self._daily_threshold:
            day_key = entry_ts.strftime("%Y-%m-%d")
            if day_key not in self._alerted_day_keys:
                self._alerted_day_keys.add(day_key)
                self._log_alert("daily", daily_total, self._daily_threshold, entry_ts)
                record_cost_alert_triggered("daily")
                triggered.append("daily")
        return triggered

    def top_skills(self, window: str, now: datetime | None = None) -> list[tuple[str, float]]:
        """
        获取指定窗口内成本最高的三个技能。

        功能:
            - 计算指定窗口内的总成本
            - 排序并返回成本最高的三个技能
        """
        current = now or datetime.now()
        values: dict[str, float] = {}
        for entry in self._window_entries(window=window, now=current):
            values[entry.skill] = values.get(entry.skill, 0.0) + entry.cost
        ranked = sorted(values.items(), key=lambda item: item[1], reverse=True)
        return [(name, round(cost, 6)) for name, cost in ranked[:3]]

    def check_call_allowed(self, operation: str, now: datetime | None = None) -> tuple[bool, str]:
        """
        检查是否允许新的调用。

        功能:
            - 检查成本是否超过每日阈值
            - 根据成本熔断器设置决定是否允许新的调用
        """
        current = now or datetime.now()
        self._prune(current)
        if not self._circuit_breaker_enabled:
            return True, ""
        if self._daily_threshold <= 0:
            return True, ""
        daily_total = self._window_total("daily", now=current)
        if daily_total < self._daily_threshold:
            return True, ""
        guidance = "当前服务预算已达到当日阈值，暂不支持新的智能调用，请稍后再试。"
        logger.warning(
            "成本熔断触发，拒绝新调用",
            extra={
                "event_code": "cost.monitor.circuit_breaker.blocked",
                "operation": str(operation or "unknown"),
                "daily_total": round(daily_total, 6),
                "daily_threshold": self._daily_threshold,
            },
        )
        return False, guidance

    def _window_total(self, window: str, now: datetime) -> float:
        """
        计算指定窗口内的总成本。

        功能:
            - 汇总指定窗口内的所有成本
        """
        return round(sum(item.cost for item in self._window_entries(window=window, now=now)), 6)

    def _window_entries(self, window: str, now: datetime) -> list[_CostEntry]:
        """
        获取指定窗口内的成本记录。

        功能:
            - 根据窗口类型筛选成本记录
        """
        if window == "hourly":
            threshold = now - timedelta(hours=1)
            return [item for item in self._entries if item.ts >= threshold and item.ts <= now]
        if window == "daily":
            day_start = datetime(now.year, now.month, now.day)
            return [item for item in self._entries if item.ts >= day_start and item.ts <= now]
        return []

    def _prune(self, now: datetime) -> None:
        """
        清理过期的成本记录。

        功能:
            - 移除超过两天的成本记录
            - 更新已触发警报的小时和日键
        """
        threshold = now - timedelta(days=2)
        while self._entries and self._entries[0].ts < threshold:
            self._entries.popleft()
        current_day_key = now.strftime("%Y-%m-%d")
        self._alerted_day_keys = {key for key in self._alerted_day_keys if key >= current_day_key}
        current_hour_prefix = now.strftime("%Y-%m-%dT")
        self._alerted_hour_keys = {key for key in self._alerted_hour_keys if key.startswith(current_hour_prefix)}

    def _log_alert(self, window: str, current_total: float, threshold: float, now: datetime) -> None:
        """
        记录成本警报日志。

        功能:
            - 记录警报信息到日志
        """
        logger.warning(
            "成本阈值触发告警",
            extra={
                "event_code": "cost.monitor.threshold_breached",
                "window": window,
                "current_total": round(current_total, 6),
                "threshold": round(threshold, 6),
                "top3_cost_skills": self.top_skills(window=window, now=now),
            },
        )

    def _normalize_ts(self, ts: datetime | str | None) -> datetime:
        """
        标准化时间戳。

        功能:
            - 将输入的时间戳转换为 datetime 对象
        """
        if isinstance(ts, datetime):
            return ts
        if isinstance(ts, str) and ts.strip():
            try:
                return datetime.fromisoformat(ts.strip())
            except Exception:
                return datetime.now()
        return datetime.now()


_GLOBAL_COST_MONITOR: CostMonitor | None = None


def configure_cost_monitor(config: CostMonitorConfig) -> CostMonitor:
    """
    配置全局成本监控器。

    功能:
        - 初始化全局成本监控器实例
    """
    global _GLOBAL_COST_MONITOR
    _GLOBAL_COST_MONITOR = CostMonitor(
        hourly_threshold=float(config.hourly_threshold),
        daily_threshold=float(config.daily_threshold),
        circuit_breaker_enabled=bool(config.circuit_breaker_enabled),
    )
    return _GLOBAL_COST_MONITOR


def get_cost_monitor() -> CostMonitor | None:
    """
    获取全局成本监控器实例。

    功能:
        - 返回全局成本监控器实例
    """
    return _GLOBAL_COST_MONITOR
