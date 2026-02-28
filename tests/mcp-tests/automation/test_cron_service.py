from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time
from typing import Any

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


def test_notify_cron_execution_posts_contract_payload(tmp_path: Path, monkeypatch: Any) -> None:
    service = _build_service(tmp_path)
    service._settings.automation.notify_webhook_url = "https://notify.example.com/notify"
    service._settings.automation.notify_api_key = "notify-key"
    service._settings.automation.notify_timeout_seconds = 3

    sent: dict[str, Any] = {}

    class _FakeResponse:
        status_code = 200
        text = "ok"

    class _FakeAsyncClient:
        def __init__(self, timeout: float, trust_env: bool) -> None:
            sent["timeout"] = timeout
            sent["trust_env"] = trust_env

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return None

        async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]):
            sent["url"] = url
            sent["json"] = json
            sent["headers"] = headers
            return _FakeResponse()

    monkeypatch.setattr("src.automation.service.httpx.AsyncClient", _FakeAsyncClient)

    result = asyncio.run(
        service.notify_cron_execution_result(
            job_id="job-1",
            status="failed",
            payload={
                "action": {"type": "log.write", "message": "tick"},
                "context": {"notify_target": {"chat_id": "oc_notify"}},
            },
            error_detail="cron run failed",
        )
    )

    assert result["status"] == "sent"
    body = sent["json"]
    assert body["event"] == "automation.completed"
    assert body["job_type"] == "cron"
    assert body["job_id"] == "job-1"
    assert body["status"] == "failed"
    assert body["notify_target"]["chat_id"] == "oc_notify"
    assert "JobID: job-1" in body["error"]
    assert sent["headers"]["x-automation-key"] == "notify-key"


def test_rule_notify_target_prefers_rule_chat_id(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    target = service._resolve_rule_notify_target(
        rule={
            "rule_id": "R1",
            "notify_chat_id": "oc_rule",
        },
        inherited_target={
            "chat_id": "oc_inherited",
            "user_id": "ou_user",
        },
    )

    assert target["chat_id"] == "oc_rule"
    assert target["user_id"] == "ou_user"


def test_notify_rule_execution_results_prefers_rule_notify_chat_id(tmp_path: Path) -> None:
    rules_path = tmp_path / "automation_rules.yaml"
    rules_path.write_text(
        """
rules:
  - rule_id: R_NOTIFY
    name: 测试规则
    enabled: true
    table:
      table_id: tbl_1
    notify_chat_id: oc_rule
    trigger:
      any_field_changed: true
    pipeline:
      actions:
        - type: log.write
          message: ok
""".strip(),
        encoding="utf-8",
    )

    service = _build_service(tmp_path)
    captured: list[dict[str, Any]] = []

    async def _fake_notify(*, job_type: str, job_id: str, status: str, summary: str, notify_target: dict[str, str], error: str = "") -> dict[str, Any]:
        captured.append(
            {
                "job_type": job_type,
                "job_id": job_id,
                "status": status,
                "summary": summary,
                "notify_target": dict(notify_target),
                "error": error,
            }
        )
        return {"status": "sent"}

    service._notify_automation_completed = _fake_notify  # type: ignore[method-assign]

    asyncio.run(
        service._notify_rule_execution_results(
            app_token="",
            table_id="tbl_1",
            record_id="rec_1",
            event_kind="updated",
            rule_execution={
                "results": [
                    {
                        "rule_id": "R_NOTIFY",
                        "name": "测试规则",
                        "status": "success",
                        "error": "",
                    }
                ]
            },
            inherited_notify_target={
                "chat_id": "oc_inherited",
                "user_id": "ou_1",
            },
        )
    )

    assert len(captured) == 1
    assert captured[0]["job_type"] == "rule"
    assert captured[0]["job_id"] == "R_NOTIFY"
    assert captured[0]["notify_target"]["chat_id"] == "oc_rule"
    assert captured[0]["notify_target"]["user_id"] == "ou_1"
