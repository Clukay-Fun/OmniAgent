"""
Prometheus 指标模块

功能：
- 请求计数器
- 技能执行延迟直方图
- 技能成功/失败计数
- 活跃会话数
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
    
    # MCP 工具调用计数
    MCP_TOOL_CALL_COUNT = Counter(
        "feishu_agent_mcp_tool_calls_total",
        "Total number of MCP tool calls",
        ["tool_name", "status"],
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
    ACTIVE_SESSIONS = DummyMetric()
    CONFIG_RELOAD_COUNT = DummyMetric()
# endregion
# ============================================


# ============================================
# region 指标记录辅助函数
# ============================================
def record_request(endpoint: str, status: str = "success") -> None:
    """记录请求"""
    REQUEST_COUNT.labels(endpoint=endpoint, status=status).inc()


def record_skill_execution(skill_name: str, status: str, duration: float) -> None:
    """记录技能执行"""
    SKILL_EXECUTION_COUNT.labels(skill_name=skill_name, status=status).inc()
    SKILL_EXECUTION_DURATION.labels(skill_name=skill_name).observe(duration)


def record_intent_parse(method: str, duration: float) -> None:
    """记录意图解析"""
    INTENT_PARSE_DURATION.labels(method=method).observe(duration)


def record_llm_call(operation: str, status: str, duration: float) -> None:
    """记录 LLM 调用"""
    LLM_CALL_COUNT.labels(operation=operation, status=status).inc()
    LLM_CALL_DURATION.labels(operation=operation).observe(duration)


def record_mcp_tool_call(tool_name: str, status: str) -> None:
    """记录 MCP 工具调用"""
    MCP_TOOL_CALL_COUNT.labels(tool_name=tool_name, status=status).inc()


def set_active_sessions(count: int) -> None:
    """设置活跃会话数"""
    ACTIVE_SESSIONS.set(count)


def record_config_reload(config_file: str, status: str) -> None:
    """记录配置重载"""
    CONFIG_RELOAD_COUNT.labels(config_file=config_file, status=status).inc()
# endregion
# ============================================


# ============================================
# region 装饰器
# ============================================
def track_skill_execution(skill_name: str):
    """
    技能执行指标追踪装饰器
    
    用法：
        @track_skill_execution("QuerySkill")
        async def execute(self, context):
            ...
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
    LLM 调用指标追踪装饰器
    
    用法：
        @track_llm_call("chat")
        async def chat(self, prompt):
            ...
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
# ============================================


# ============================================
# region 指标导出
# ============================================
def get_metrics() -> bytes:
    """获取 Prometheus 格式的指标数据"""
    if PROMETHEUS_AVAILABLE:
        return generate_latest()
    return b"# Prometheus client not installed\n"


def get_metrics_content_type() -> str:
    """获取指标数据的 Content-Type"""
    if PROMETHEUS_AVAILABLE:
        return CONTENT_TYPE_LATEST
    return "text/plain"
# endregion
# ============================================
