"""
Logging setup utilities.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.config import LoggingSettings


class JsonFormatter(logging.Formatter):
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


def setup_logging(settings: LoggingSettings) -> None:
    level = getattr(logging, settings.level.upper(), logging.INFO)
    handler = logging.StreamHandler()
    if settings.format == "json":
        handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler])
