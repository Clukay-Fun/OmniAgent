from __future__ import annotations

import json
import logging
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.utils.logger import (  # noqa: E402
    StructuredJsonFormatter,
    clear_request_context,
    set_request_context,
)


def test_structured_logger_keeps_chinese_message_and_event_code() -> None:
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="请求处理完成",
        args=(),
        exc_info=None,
    )
    record.event_code = "orchestrator.request.completed"

    payload = json.loads(formatter.format(record))
    assert payload["message"] == "请求处理完成"
    assert payload["event_code"] == "orchestrator.request.completed"


def test_structured_logger_includes_request_context() -> None:
    formatter = StructuredJsonFormatter()
    set_request_context(request_id="req-001", user_id="u-001")

    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="开始执行技能",
        args=(),
        exc_info=None,
    )
    record.event_code = "router.skill.start"
    payload = json.loads(formatter.format(record))

    assert payload["request_id"] == "req-001"
    assert payload["user_id"] == "u-001"
    assert payload["event_code"] == "router.skill.start"

    clear_request_context()
