from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time

import pytest


MCP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_ROOT))

from src.automation.cron_store import PAUSED, CronJob
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
        cron_queue_file=str(tmp_path / "automation_data" / "cron_queue.jsonl"),
        cron_max_consecutive_failures=2,
    )
    settings = Settings(automation=automation)
    return AutomationService(settings, _FakeClient())


def test_create_cron_job_and_list(tmp_path: Path) -> None:
    service = _build_service(tmp_path)

    created = service.create_cron_job(
        cron_expr="*/5 * * * *",
        action={"type": "log.write", "message": "tick"},
        context={"rule_id": "rule-1"},
        app_token="app_1",
        table_id="tbl_1",
        record_id="rec_1",
        rule_id="rule-1",
    )

    assert created["status"] == "scheduled"
    items = service.list_cron_jobs(status="active", limit=10)
    assert len(items) == 1
    assert items[0]["job_id"] == created["job_id"]
    assert items[0]["action_type"] == "log.write"


def test_create_cron_job_rejects_invalid_expression(tmp_path: Path) -> None:
    service = _build_service(tmp_path)

    with pytest.raises(AutomationValidationError, match="invalid cron expression"):
        service.create_cron_job(
            cron_expr="invalid cron",
            action={"type": "log.write", "message": "tick"},
        )


def test_resume_and_cancel_cron_job_status_flow(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    service.cron_store.schedule(
        CronJob(
            job_id="job-paused",
            cron_expr="*/5 * * * *",
            payload={"action": {"type": "log.write", "message": "tick"}},
            status=PAUSED,
            next_run_at=time.time() - 10,
            max_consecutive_failures=2,
        )
    )

    resumed = service.resume_cron_job("job-paused")
    again = service.resume_cron_job("job-paused")
    cancelled = service.cancel_cron_job("job-paused")
    cancelled_again = service.cancel_cron_job("job-paused")

    assert resumed["status"] == "resumed"
    assert resumed["current_status"] == "active"
    assert again["status"] == "not_resumable"
    assert cancelled["status"] == "cancelled"
    assert cancelled_again["status"] == "not_cancellable"


def test_execute_cron_payload_runs_action_executor(tmp_path: Path) -> None:
    service = _build_service(tmp_path)

    result = asyncio.run(
        service.execute_cron_payload(
            {
                "action": {"type": "log.write", "message": "cron ok"},
                "context": {"rule_id": "rule-1"},
                "app_token": "app_1",
                "table_id": "tbl_1",
                "record_id": "rec_1",
            }
        )
    )

    assert isinstance(result, list)
    assert result[0]["type"] == "log.write"
