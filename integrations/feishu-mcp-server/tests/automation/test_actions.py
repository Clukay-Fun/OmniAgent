from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time

import pytest


MCP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_ROOT))

from src.automation.actions import ActionExecutionError, ActionExecutor
from src.automation.delay_store import DelayStore
from src.config import AutomationSettings, CalendarSettings, Settings


class _FakeClient:
    async def request(self, method: str, path: str, json_body: dict | None = None) -> dict:
        _ = (method, path, json_body)
        return {"data": {"event": {"event_id": "evt_1", "url": "https://example.com/e/1"}}}


def _build_settings(
    tmp_path: Path,
    *,
    http_allowed_domains: list[str] | None = None,
    default_calendar_id: str = "",
) -> Settings:
    return Settings(
        automation=AutomationSettings(
            enabled=True,
            storage_dir=str(tmp_path),
            action_max_retries=0,
            action_retry_delay_seconds=0.0,
            http_allowed_domains=http_allowed_domains or [],
            http_timeout_seconds=1.0,
            schema_runtime_state_file=str(tmp_path / "schema_runtime_state.json"),
        ),
        calendar=CalendarSettings(default_calendar_id=default_calendar_id),
    )


def test_delay_action_schedules_task(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    store = DelayStore(tmp_path / "delay_queue.jsonl")
    executor = ActionExecutor(settings=settings, client=_FakeClient(), delay_store=store)

    before = time.time()
    results = asyncio.run(
        executor.run_actions(
            actions=[
                {
                    "type": "delay",
                    "delay_seconds": 2,
                    "then": {"type": "log.write", "message": "later {event_id}"},
                }
            ],
            context={"rule_id": "rule-1", "event_id": "evt_1"},
            app_token="app_1",
            table_id="tbl_1",
            record_id="rec_1",
        )
    )

    assert results[0]["type"] == "delay"
    assert results[0]["scheduled"] is True

    tasks = store.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].rule_id == "rule-1"
    assert tasks[0].payload["action"]["type"] == "log.write"
    assert tasks[0].payload["app_token"] == "app_1"
    assert before + 2 <= tasks[0].trigger_at <= time.time() + 2.2


def test_delay_action_requires_then(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    store = DelayStore(tmp_path / "delay_queue.jsonl")
    executor = ActionExecutor(settings=settings, client=_FakeClient(), delay_store=store)

    with pytest.raises(ActionExecutionError) as excinfo:
        asyncio.run(
            executor.run_actions(
                actions=[{"type": "delay", "delay_seconds": 2}],
                context={"rule_id": "rule-1"},
                app_token="app_1",
                table_id="tbl_1",
                record_id="rec_1",
            )
        )

    assert "delay action requires then object" in str(excinfo.value)


def test_delay_action_allows_zero_seconds(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    store = DelayStore(tmp_path / "delay_queue.jsonl")
    executor = ActionExecutor(settings=settings, client=_FakeClient(), delay_store=store)

    results = asyncio.run(
        executor.run_actions(
            actions=[
                {
                    "type": "delay",
                    "delay_seconds": 0,
                    "then": {"type": "log.write", "message": "instant"},
                }
            ],
            context={"rule_id": "rule-1"},
            app_token="app_1",
            table_id="tbl_1",
            record_id="rec_1",
        )
    )

    assert results[0]["scheduled"] is True


def test_delay_action_rejects_too_large_seconds(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    settings.automation.delay_max_seconds = 10
    store = DelayStore(tmp_path / "delay_queue.jsonl")
    executor = ActionExecutor(settings=settings, client=_FakeClient(), delay_store=store)

    with pytest.raises(ActionExecutionError) as excinfo:
        asyncio.run(
            executor.run_actions(
                actions=[
                    {
                        "type": "delay",
                        "delay_seconds": 999,
                        "then": {"type": "log.write", "message": "later"},
                    }
                ],
                context={"rule_id": "rule-1"},
                app_token="app_1",
                table_id="tbl_1",
                record_id="rec_1",
            )
        )

    assert "exceeds max" in str(excinfo.value)


def test_http_request_rejects_non_allowlisted_domain(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path, http_allowed_domains=["allowed.example.com"])
    executor = ActionExecutor(settings=settings, client=_FakeClient())

    with pytest.raises(ActionExecutionError) as excinfo:
        asyncio.run(
            executor.run_actions(
                actions=[
                    {
                        "type": "http.request",
                        "method": "GET",
                        "url": "https://not-allowed.example.com/path",
                    }
                ],
                context={},
                app_token="",
                table_id="",
                record_id="",
            )
        )

    assert "host not in allowlist" in str(excinfo.value)


def test_calendar_create_requires_calendar_id(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path, default_calendar_id="")
    executor = ActionExecutor(settings=settings, client=_FakeClient())

    with pytest.raises(ActionExecutionError) as excinfo:
        asyncio.run(
            executor.run_actions(
                actions=[{"type": "calendar.create", "summary": "s", "start_at": "2026-02-25 10:00"}],
                context={},
                app_token="",
                table_id="",
                record_id="",
            )
        )

    assert "requires calendar_id" in str(excinfo.value)
