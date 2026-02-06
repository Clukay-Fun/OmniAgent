"""
描述: MCP Server 日志工具库
主要功能:
    - JSON 格式化输出
    - 统一日志配置初始化
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.config import LoggingSettings


# region 日志 Formatter
class JsonFormatter(logging.Formatter):
    """简单 JSON 日志格式化器"""
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)
# endregion


# region 日志初始化
def setup_logging(settings: LoggingSettings) -> None:
    """
    初始化日志系统

    参数:
        settings: 日志配置对象
    """
    level = getattr(logging, settings.level.upper(), logging.INFO)
    handler = logging.StreamHandler()
    if settings.format == "json":
        handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler])
# endregion
