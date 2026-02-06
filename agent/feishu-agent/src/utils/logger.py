"""
描述: 结构化日志工具库
主要功能:
    - JSON 格式结构化输出 (Structured Logging)
    - 自动追踪请求上下文 (Request ID, User ID)
    - 超时与性能指标自动记录
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar
from typing import Any, Callable
from functools import wraps

from src.config import LoggingSettings


# region 上下文变量 (Context Vars)
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
user_id_var: ContextVar[str] = ContextVar("user_id", default="")
skill_name_var: ContextVar[str] = ContextVar("skill_name", default="")
# endregion


# region 日志 Formatter
class StructuredJsonFormatter(logging.Formatter):
    """
    JSON 结构化日志格式化器

    功能:
        - 将日志记录转换为符合 ELK/Loki 标准的 JSON 格式
        - 自动注入当前上下文变量
    """
    
    def format(self, record: logging.LogRecord) -> str:
        # 基础字段
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # 上下文字段
        if request_id := request_id_var.get():
            payload["request_id"] = request_id
        if user_id := user_id_var.get():
            payload["user_id"] = user_id
        if skill_name := skill_name_var.get():
            payload["skill"] = skill_name
        
        # extra 字段（通过 logger.info("msg", extra={...}) 传入）
        if hasattr(record, "__dict__"):
            for key, value in record.__dict__.items():
                if key not in (
                    "name", "msg", "args", "created", "filename", "funcName",
                    "levelname", "levelno", "lineno", "module", "msecs",
                    "pathname", "process", "processName", "relativeCreated",
                    "stack_info", "exc_info", "exc_text", "thread", "threadName",
                    "message", "asctime",
                ):
                    if not key.startswith("_"):
                        payload[key] = value
        
        # 异常信息
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        
        return json.dumps(payload, ensure_ascii=False, default=str)


class SimpleFormatter(logging.Formatter):
    """简单文本格式化器（开发环境用）"""
    
    def format(self, record: logging.LogRecord) -> str:
        base = f"[{self.formatTime(record)}] {record.levelname:5} {record.name}: {record.getMessage()}"
        
        # 添加上下文
        context_parts = []
        if request_id := request_id_var.get():
            context_parts.append(f"req={request_id[:8]}")
        if user_id := user_id_var.get():
            context_parts.append(f"user={user_id[:8]}")
        if skill_name := skill_name_var.get():
            context_parts.append(f"skill={skill_name}")
        
        if context_parts:
            base += f" ({', '.join(context_parts)})"
        
        # extra 字段
        extras = []
        for key in ("query", "score", "duration_ms"):
            if hasattr(record, key):
                extras.append(f"{key}={getattr(record, key)}")
        if extras:
            base += f" [{', '.join(extras)}]"
        
        return base
# endregion


# region 上下文管理
def set_request_context(
    request_id: str | None = None,
    user_id: str | None = None,
    skill_name: str | None = None,
) -> None:
    """
    设置当前请求的上下文信息

    参数:
        request_id: 请求唯一标识
        user_id: 用户标识
        skill_name: 当前执行的 Skill 名称
    """
    if request_id:
        request_id_var.set(request_id)
    if user_id:
        user_id_var.set(user_id)
    if skill_name:
        skill_name_var.set(skill_name)


def clear_request_context() -> None:
    """清除请求上下文"""
    request_id_var.set("")
    user_id_var.set("")
    skill_name_var.set("")


def generate_request_id() -> str:
    """生成请求 ID"""
    return str(uuid.uuid4())[:12]
# endregion


# region 性能监控
def log_duration(logger_name: str = __name__):
    """
    执行耗时记录装饰器

    参数:
        logger_name: 用于输出日志的 Logger 名称
    
    效果:
        - 自动计算异步/同步函数的执行耗时
        - 输出包含 duration_ms 的 debug/error 日志
    """
    def decorator(func: Callable):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            logger = logging.getLogger(logger_name)
            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000
                logger.debug(
                    f"{func.__name__} completed",
                    extra={"duration_ms": round(duration_ms, 2)},
                )
                return result
            except Exception as e:
                duration_ms = (time.perf_counter() - start) * 1000
                logger.error(
                    f"{func.__name__} failed",
                    extra={"duration_ms": round(duration_ms, 2), "error": str(e)},
                )
                raise
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            logger = logging.getLogger(logger_name)
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000
                logger.debug(
                    f"{func.__name__} completed",
                    extra={"duration_ms": round(duration_ms, 2)},
                )
                return result
            except Exception as e:
                duration_ms = (time.perf_counter() - start) * 1000
                logger.error(
                    f"{func.__name__} failed",
                    extra={"duration_ms": round(duration_ms, 2), "error": str(e)},
                )
                raise
        
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator
# endregion


# region 初始化配置
def setup_logging(settings: LoggingSettings) -> None:
    """
    初始化全局日志配置

    参数:
        settings: 日志配置对象
    
    动作:
        - 配置 Root Logger 级别
        - 设置 StreamHandler 及 Formatter (JSON/Text)
        - 调整第三方库日志级别以减少噪音
    """
    level = getattr(logging, settings.level.upper(), logging.INFO)
    
    # 创建 handler
    handler = logging.StreamHandler()
    
    # 根据配置选择 formatter
    if settings.format == "json":
        handler.setFormatter(StructuredJsonFormatter())
    else:
        handler.setFormatter(SimpleFormatter())
    
    # 配置根 logger
    logging.basicConfig(level=level, handlers=[handler], force=True)
    
    # 降低第三方库日志级别
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
# endregion
