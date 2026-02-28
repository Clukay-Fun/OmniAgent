from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any, cast

import pytest


MCP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_ROOT))

from src.config import AutomationSettings, Settings
from src.tools.automation import AutomationCronTool, _parse_schedule_text_to_cron
from src.tools.base import ToolContext


class _FakeClient:
    async def request(self, method: str, path: str, json_body: dict | None = None) -> dict:
        _ = (method, path, json_body)
        return {"data": {}}


def _build_context() -> ToolContext:
    settings = Settings(automation=AutomationSettings(enabled=True))
    return ToolContext(settings=settings, client=cast(Any, _FakeClient()))


def test_parse_schedule_text_daily_pattern() -> None:
    cron_expr = _parse_schedule_text_to_cron("每天早上9点发日报")
    assert cron_expr == "0 9 * * *"


def test_cron_tool_create_operation_uses_resolved_cron(monkeypatch) -> None:
    class _FakeAutomationService:
        def __init__(self, settings, client) -> None:
            _ = (settings, client)

        def create_cron_job(self, **kwargs):
            return {
                "status": "scheduled",
                "job_id": "job-1",
                "cron_expr": kwargs["cron_expr"],
            }

    import src.tools.automation as automation_tool_module

    monkeypatch.setattr(automation_tool_module, "AutomationService", _FakeAutomationService)

    tool = AutomationCronTool(_build_context())
    result = asyncio.run(
        tool.run(
            {
                "operation": "create",
                "schedule_text": "每天早上9点发日报",
                "action": {"type": "log.write", "message": "report"},
            }
        )
    )

    assert result["status"] == "scheduled"
    assert result["cron_expr"] == "0 9 * * *"
    assert result["operation"] == "create"


def test_cron_tool_list_operation(monkeypatch) -> None:
    class _FakeAutomationService:
        def __init__(self, settings, client) -> None:
            _ = (settings, client)

        def list_cron_jobs(self, *, status=None, limit=100):
            _ = (status, limit)
            return [{"job_id": "job-1", "status": "active"}]

    import src.tools.automation as automation_tool_module

    monkeypatch.setattr(automation_tool_module, "AutomationService", _FakeAutomationService)

    tool = AutomationCronTool(_build_context())
    result = asyncio.run(tool.run({"operation": "list", "status": "active", "limit": 20}))

    assert result["operation"] == "list"
    assert result["count"] == 1
    assert result["items"][0]["job_id"] == "job-1"


def test_cron_tool_rejects_missing_required_params() -> None:
    tool = AutomationCronTool(_build_context())

    with pytest.raises(ValueError, match="cron or schedule_text is required"):
        asyncio.run(
            tool.run(
                {
                    "operation": "create",
                    "action": {"type": "log.write", "message": "x"},
                }
            )
        )


def test_cron_tool_rejects_invalid_operation() -> None:
    tool = AutomationCronTool(_build_context())

    with pytest.raises(ValueError, match="operation must be one of"):
        asyncio.run(tool.run({"operation": "invalid_op"}))
