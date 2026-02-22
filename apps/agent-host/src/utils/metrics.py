"""
描述: Prometheus 指标收集模块
主要功能:
    - 定义核心业务指标 (Counter, Histogram, Gauge)
    - 提供指标记录的工具函数与装饰器
    - 兼容 Prometheus 客户端未安装的降级模式
"""

from __future__ import annotations

import time
from typing import Any, Callable
from functools import wraps

try:
    from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False


# ============================================
# region 指标定义
# ============================================
if PROMETHEUS_AVAILABLE:
    # 请求计数器
    REQUEST_COUNT = Counter(
        "feishu_agent_requests_total",
        "Total number of requests received",
        ["endpoint", "status"],
    )
    
    # 技能执行计数器
    SKILL_EXECUTION_COUNT = Counter(
        "feishu_agent_skill_executions_total",
        "Total number of skill executions",
        ["skill_name", "status"],
    )
    
    # 技能执行延迟直方图
    SKILL_EXECUTION_DURATION = Histogram(
        "feishu_agent_skill_execution_duration_seconds",
        "Skill execution duration in seconds",
        ["skill_name"],
        buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )
    
    # 意图解析延迟
    INTENT_PARSE_DURATION = Histogram(
        "feishu_agent_intent_parse_duration_seconds",
        "Intent parsing duration in seconds",
        ["method"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0),
    )
    
    # LLM 调用计数
    LLM_CALL_COUNT = Counter(
        "feishu_agent_llm_calls_total",
        "Total number of LLM API calls",
        ["operation", "status"],
    )
    
    # LLM 调用延迟
    LLM_CALL_DURATION = Histogram(
        "feishu_agent_llm_call_duration_seconds",
        "LLM API call duration in seconds",
        ["operation"],
        buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
    )

    # Reminder 推送计数
    REMINDER_PUSH_COUNT = Counter(
        "feishu_agent_reminder_push_total",
        "Total number of reminder pushes",
        ["status"],
    )
    
    # MCP 工具调用计数
    MCP_TOOL_CALL_COUNT = Counter(
        "feishu_agent_mcp_tool_calls_total",
        "Total number of MCP tool calls",
        ["tool_name", "status"],
    )

    FEISHU_EVENT_COUNT = Counter(
        "feishu_agent_events_total",
        "Total number of Feishu events by type",
        ["event_type", "status"],
    )

    CHITCHAT_GUARD_COUNT = Counter(
        "feishu_agent_chitchat_guard_total",
        "Total number of chitchat guard routing decisions",
        ["route"],
    )

    SCHEMA_WATCHER_ALERT_COUNT = Counter(
        "schema_watcher_alerts_total",
        "Total schema watcher alerts by change type",
        ["change_type"],
    )

    AUTOMATION_ENQUEUE_COUNT = Counter(
        "automation_enqueue_total",
        "Total automation enqueue attempts by event type and status",
        ["event_type", "status"],
    )

    AUTOMATION_CONSUMED_COUNT = Counter(
        "automation_consumed_total",
        "Total automation consumed records by event type and status",
        ["event_type", "status"],
    )

    FIELD_FORMAT_COUNT = Counter(
        "field_format_total",
        "Total field formatting outcomes by type and status",
        ["type", "status"],
    )

    CARD_TEMPLATE_COUNT = Counter(
        "card_template_total",
        "Total card template render outcomes",
        ["template_id", "status"],
    )
    
    # 活跃会话数
    ACTIVE_SESSIONS = Gauge(
        "feishu_agent_active_sessions",
        "Number of active user sessions",
    )
    
    # 配置热更新计数
    CONFIG_RELOAD_COUNT = Counter(
        "feishu_agent_config_reloads_total",
        "Total number of config reloads",
        ["config_file", "status"],
    )
else:
    # Prometheus 不可用时的空实现
    class DummyMetric:
        def labels(self, *args, **kwargs): return self
        def inc(self, *args, **kwargs): pass
        def dec(self, *args, **kwargs): pass
        def set(self, *args, **kwargs): pass
        def observe(self, *args, **kwargs): pass
    
    REQUEST_COUNT = DummyMetric()
    SKILL_EXECUTION_COUNT = DummyMetric()
    SKILL_EXECUTION_DURATION = DummyMetric()
    INTENT_PARSE_DURATION = DummyMetric()
    LLM_CALL_COUNT = DummyMetric()
    LLM_CALL_DURATION = DummyMetric()
    MCP_TOOL_CALL_COUNT = DummyMetric()
    FEISHU_EVENT_COUNT = DummyMetric()
    CHITCHAT_GUARD_COUNT = DummyMetric()
    SCHEMA_WATCHER_ALERT_COUNT = DummyMetric()
    AUTOMATION_ENQUEUE_COUNT = DummyMetric()
    AUTOMATION_CONSUMED_COUNT = DummyMetric()
    FIELD_FORMAT_COUNT = DummyMetric()
    CARD_TEMPLATE_COUNT = DummyMetric()
    ACTIVE_SESSIONS = DummyMetric()
    CONFIG_RELOAD_COUNT = DummyMetric()
    REMINDER_PUSH_COUNT = DummyMetric()
# endregion


# region 指标记录工具函数
def record_request(endpoint: str, status: str = "success") -> None:
    """记录 API 请求"""
    REQUEST_COUNT.labels(endpoint=endpoint, status=status).inc()


def record_skill_execution(skill_name: str, status: str, duration: float) -> None:
    """记录技能执行状态与耗时"""
    SKILL_EXECUTION_COUNT.labels(skill_name=skill_name, status=status).inc()
    SKILL_EXECUTION_DURATION.labels(skill_name=skill_name).observe(duration)


def record_intent_parse(method: str, duration: float) -> None:
    """记录意图解析耗时"""
    INTENT_PARSE_DURATION.labels(method=method).observe(duration)


def record_llm_call(operation: str, status: str, duration: float) -> None:
    """记录 LLM 调用指标"""
    LLM_CALL_COUNT.labels(operation=operation, status=status).inc()
    LLM_CALL_DURATION.labels(operation=operation).observe(duration)


def record_mcp_tool_call(tool_name: str, status: str) -> None:
    """记录 MCP 工具调用结果"""
    MCP_TOOL_CALL_COUNT.labels(tool_name=tool_name, status=status).inc()


def record_feishu_event(event_type: str, status: str) -> None:
    """记录飞书事件分发结果。"""
    FEISHU_EVENT_COUNT.labels(event_type=event_type, status=status).inc()


def record_chitchat_guard(route: str) -> None:
    """记录闲聊门控决策。"""
    CHITCHAT_GUARD_COUNT.labels(route=route).inc()


def record_schema_watcher_alert(change_type: str) -> None:
    """记录 schema watcher 告警事件。"""
    SCHEMA_WATCHER_ALERT_COUNT.labels(change_type=change_type).inc()


def record_automation_enqueue(event_type: str, status: str) -> None:
    """记录 automation enqueue 结果。"""
    AUTOMATION_ENQUEUE_COUNT.labels(event_type=event_type, status=status).inc()


def record_automation_consumed(event_type: str, status: str) -> None:
    """记录 automation consumer 处理结果。"""
    AUTOMATION_CONSUMED_COUNT.labels(event_type=event_type, status=status).inc()


def record_field_format(field_type: str, status: str) -> None:
    """记录字段格式化结果。"""
    FIELD_FORMAT_COUNT.labels(type=field_type, status=status).inc()


def record_card_template(template_id: str, status: str) -> None:
    """记录卡片模板渲染结果。"""
    CARD_TEMPLATE_COUNT.labels(template_id=template_id, status=status).inc()


def set_active_sessions(count: int) -> None:
    """更新当前活跃会话数 (Gauge)"""
    ACTIVE_SESSIONS.set(count)


def record_config_reload(config_file: str, status: str) -> None:
    """记录配置热重载事件"""
    CONFIG_RELOAD_COUNT.labels(config_file=config_file, status=status).inc()


def record_reminder_push(status: str) -> None:
    """记录提醒推送结果"""
    REMINDER_PUSH_COUNT.labels(status=status).inc()
# endregion


# region 监控装饰器
def track_skill_execution(skill_name: str):
    """
    技能执行监控装饰器

    参数:
        skill_name: 技能名称
    
    功能:
        - 自动捕获异常并标记为失败
        - 记录执行成功/失败计数与耗时
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            status = "success"
            try:
                result = await func(*args, **kwargs)
                if hasattr(result, "success") and not result.success:
                    status = "failure"
                return result
            except Exception:
                status = "error"
                raise
            finally:
                duration = time.perf_counter() - start
                record_skill_execution(skill_name, status, duration)
        return wrapper
    return decorator


def track_llm_call(operation: str):
    """
    LLM 调用监控装饰器

    参数:
        operation: 操作类型 (e.g. 'chat', 'embedding')
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            status = "success"
            try:
                return await func(*args, **kwargs)
            except Exception:
                status = "error"
                raise
            finally:
                duration = time.perf_counter() - start
                record_llm_call(operation, status, duration)
        return wrapper
    return decorator
# endregion


# region 指标导出接口
def get_metrics() -> bytes:
    """获取 Prometheus 格式的指标数据 (用于 /metrics 端点)"""
    if PROMETHEUS_AVAILABLE:
        return generate_latest()
    return b"# Prometheus client not installed\n"


def get_metrics_content_type() -> str:
    """获取指标数据的 Content-Type (适配 OpenMetrics)"""
    if PROMETHEUS_AVAILABLE:
        return CONTENT_TYPE_LATEST
    return "text/plain"
# endregion
