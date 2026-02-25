from __future__ import annotations

from pathlib import Path
import sys

import pytest


MCP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_ROOT))

from src.automation.delay_store import DelayedTask
from src.automation.delay_scheduler import DelayScheduler
from src.automation.service import AutomationService, AutomationValidationError
from src.config import AutomationSettings, Settings


class _FakeClient:
    async def request(self, method: str, path: str, json_body: dict | None = None) -> dict:
        _ = (method, path, json_body)
        return {"data": {}}


def _build_service(tmp_path: Path) -> AutomationService:
    automation = AutomationSettings(
        enabled=True,
        storage_dir=str(tmp_path / "automation_data"),
        rules_file=str(tmp_path / "automation_rules.yaml"),
        dead_letter_file=str(tmp_path / "automation_data" / "dead_letters.jsonl"),
        run_log_file=str(tmp_path / "automation_data" / "run_logs.jsonl"),
        schema_sync_enabled=False,
        schema_cache_file=str(tmp_path / "automation_data" / "schema_cache.json"),
        schema_runtime_state_file=str(tmp_path / "automation_data" / "schema_runtime_state.json"),
        delay_queue_file=str(tmp_path / "automation_data" / "delay_queue.jsonl"),
    )
    settings = Settings(automation=automation)
    return AutomationService(settings, _FakeClient())


def test_list_delay_tasks_filters_and_limits(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    service.delay_store.schedule(
        DelayedTask(
            task_id="task-1",
            rule_id="rule-1",
            trigger_at=10.0,
            payload={"action": {"type": "log.write"}},
        )
    )
    service.delay_store.schedule(
        DelayedTask(
            task_id="task-2",
            rule_id="rule-1",
            trigger_at=20.0,
            payload={"action": {"type": "http.request"}},
            status="completed",
        )
    )

    scheduled = service.list_delay_tasks(status="scheduled", limit=10)
    limited = service.list_delay_tasks(limit=1)

    assert [item["task_id"] for item in scheduled] == ["task-1"]
    assert scheduled[0]["action_type"] == "log.write"
    assert len(limited) == 1


def test_cancel_delay_task_status_flow(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    service.delay_store.schedule(
        DelayedTask(
            task_id="task-1",
            rule_id="rule-1",
            trigger_at=10.0,
            payload={"action": {"type": "log.write"}},
        )
    )

    first = service.cancel_delay_task("task-1")
    second = service.cancel_delay_task("task-1")
    missing = service.cancel_delay_task("task-404")

    assert first["status"] == "cancelled"
    assert second["status"] == "not_cancellable"
    assert second["current_status"] == "cancelled"
    assert missing["status"] == "not_found"


def test_list_delay_tasks_rejects_invalid_status(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    with pytest.raises(AutomationValidationError):
        service.list_delay_tasks(status="invalid")


def test_scheduler_recovers_due_tasks_after_service_restart(tmp_path: Path) -> None:
    service_before_restart = _build_service(tmp_path)
    service_before_restart.delay_store.schedule(
        DelayedTask(
            task_id="task-1",
            rule_id="rule-1",
            trigger_at=1.0,
            payload={
                "action": {"type": "log.write", "message": "ok"},
                "context": {"rule_id": "rule-1"},
                "app_token": "app_1",
                "table_id": "tbl_1",
                "record_id": "rec_1",
            },
        )
    )

    service_after_restart = _build_service(tmp_path)
    scheduler = DelayScheduler(service=service_after_restart, enabled=True, interval_seconds=0.1)

    import asyncio

    asyncio.run(scheduler._poll_and_execute(now_ts=2.0))

    tasks = service_after_restart.delay_store.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].status == "completed"
