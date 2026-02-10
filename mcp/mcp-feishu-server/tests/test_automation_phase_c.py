from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import yaml

from src.automation.poller import AutomationPoller
from src.automation.service import AutomationService
from src.config import Settings


class FakeFeishuClient:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.update_call_count = 0
        self.fail_calls: set[int] = set()

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        _ = params, headers
        if method == "GET" and "/records/" in path:
            record_id = path.rsplit("/", 1)[-1]
            return {
                "code": 0,
                "data": {
                    "record": {
                        "record_id": record_id,
                        "fields": self.records.get(record_id, {}),
                    }
                },
            }

        if method == "PUT" and "/records/" in path:
            self.update_call_count += 1
            if self.update_call_count in self.fail_calls:
                raise RuntimeError(f"mock update failed call#{self.update_call_count}")

            record_id = path.rsplit("/", 1)[-1]
            fields = (json_body or {}).get("fields") or {}
            if not isinstance(fields, dict):
                fields = {}
            self.records.setdefault(record_id, {}).update(fields)
            return {
                "code": 0,
                "data": {
                    "record": {
                        "record_id": record_id,
                        "fields": self.records.get(record_id, {}),
                    }
                },
            }

        if method == "POST" and path.endswith("/records/search"):
            return {
                "code": 0,
                "data": {"items": [], "has_more": False, "page_token": ""},
            }

        raise RuntimeError(f"unexpected request: {method} {path}")


def _write_rules(path: Path, rules: list[dict[str, Any]]) -> None:
    payload = {"meta": {"version": "1.1", "status": "test"}, "rules": rules}
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _build_settings(
    tmp_path: Path,
    rules_path: Path,
    dead_letter_path: Path,
    run_log_path: Path,
    max_retries: int,
    status_write_enabled: bool = True,
) -> Settings:
    return Settings.model_validate(
        {
            "bitable": {
                "default_app_token": "app_test",
                "default_table_id": "tbl_test",
            },
            "automation": {
                "enabled": True,
                "verification_token": "token_test",
                "storage_dir": str(tmp_path / "automation_data"),
                "rules_file": str(rules_path),
                "action_max_retries": max_retries,
                "action_retry_delay_seconds": 0,
                "dead_letter_file": str(dead_letter_path),
                "run_log_file": str(run_log_path),
                "status_write_enabled": status_write_enabled,
            },
        }
    )


def _event_payload(event_id: str) -> dict[str, Any]:
    return {
        "header": {
            "event_id": event_id,
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {
            "app_token": "app_test",
            "table_id": "tbl_test",
            "record_id": "rec_1",
        },
    }


def test_phase_c_action_retry_succeeds(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    dead_letter_path = tmp_path / "dead_letters.jsonl"
    run_log_path = tmp_path / "run_logs.jsonl"
    _write_rules(
        rules_path,
        [
            {
                "rule_id": "RRETRY",
                "name": "重试成功",
                "enabled": True,
                "priority": 100,
                "table": {"table_id": "tbl_test"},
                "trigger": {
                    "field": "案件分类",
                    "condition": {"changed": True, "equals": "劳动争议"},
                },
                "pipeline": {
                    "actions": [
                        {"type": "bitable.update", "fields": {"业务动作": "已执行"}},
                    ]
                },
            }
        ],
    )

    settings = _build_settings(tmp_path, rules_path, dead_letter_path, run_log_path, max_retries=1)
    client = FakeFeishuClient()
    client.fail_calls = {2}
    service = AutomationService(settings=settings, client=client)

    client.records["rec_1"] = {"案件分类": "民商事"}
    assert asyncio.run(service.handle_event(_event_payload("evt_init")))["kind"] == "initialized"

    client.records["rec_1"] = {"案件分类": "劳动争议"}
    result = asyncio.run(service.handle_event(_event_payload("evt_retry_ok")))

    assert result["rules"]["status"] == "success"
    assert client.records["rec_1"]["业务动作"] == "已执行"
    assert client.records["rec_1"]["自动化_执行状态"] == "成功"
    assert dead_letter_path.exists() is False
    lines = run_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["rule_id"] == "RRETRY"
    assert payload["result"] == "success"
    assert payload["trigger_field"] == "案件分类"
    assert payload["retry_count"] == 1


def test_phase_c_dead_letter_written_after_retry_exhausted(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    dead_letter_path = tmp_path / "dead_letters.jsonl"
    run_log_path = tmp_path / "run_logs.jsonl"
    _write_rules(
        rules_path,
        [
            {
                "rule_id": "RDLQ",
                "name": "重试失败入死信",
                "enabled": True,
                "priority": 100,
                "table": {"table_id": "tbl_test"},
                "trigger": {
                    "field": "案件分类",
                    "condition": {"changed": True, "equals": "劳动争议"},
                },
                "pipeline": {
                    "actions": [
                        {"type": "bitable.update", "fields": {"业务动作": "会失败"}},
                    ]
                },
            }
        ],
    )

    settings = _build_settings(tmp_path, rules_path, dead_letter_path, run_log_path, max_retries=1)
    client = FakeFeishuClient()
    client.fail_calls = {2, 3}
    service = AutomationService(settings=settings, client=client)

    client.records["rec_1"] = {"案件分类": "民商事"}
    assert asyncio.run(service.handle_event(_event_payload("evt_init_2")))["kind"] == "initialized"

    client.records["rec_1"] = {"案件分类": "劳动争议"}
    result = asyncio.run(service.handle_event(_event_payload("evt_retry_fail")))
    assert result["rules"]["status"] == "failed"
    assert client.records["rec_1"]["自动化_执行状态"] == "失败"

    lines = dead_letter_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["rule_id"] == "RDLQ"
    assert payload["record_id"] == "rec_1"
    assert "failed after 2 attempts" in payload["error"]

    run_logs = run_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(run_logs) == 1
    run_payload = json.loads(run_logs[0])
    assert run_payload["result"] == "failed"
    assert run_payload["sent_to_dead_letter"] is True
    assert run_payload["retry_count"] == 1


def test_phase_c_run_log_works_without_status_fields(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    dead_letter_path = tmp_path / "dead_letters.jsonl"
    run_log_path = tmp_path / "run_logs.jsonl"
    _write_rules(
        rules_path,
        [
            {
                "rule_id": "RLOG",
                "name": "仅日志",
                "enabled": True,
                "priority": 100,
                "table": {"table_id": "tbl_test"},
                "trigger": {
                    "field": "案件分类",
                    "condition": {"changed": True, "equals": "劳动争议"},
                },
                "pipeline": {
                    "actions": [{"type": "log.write", "message": "ok"}],
                },
            }
        ],
    )

    settings = _build_settings(
        tmp_path,
        rules_path,
        dead_letter_path,
        run_log_path,
        max_retries=0,
        status_write_enabled=False,
    )
    client = FakeFeishuClient()
    service = AutomationService(settings=settings, client=client)

    client.records["rec_1"] = {"案件分类": "民商事", "案号": "A-1"}
    assert asyncio.run(service.handle_event(_event_payload("evt_log_1")))["kind"] == "initialized"

    client.records["rec_1"] = {"案件分类": "劳动争议", "案号": "A-1"}
    result = asyncio.run(service.handle_event(_event_payload("evt_log_2")))
    assert result["rules"]["status"] == "success"

    lines = run_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["rule_id"] == "RLOG"
    assert payload["result"] == "success"
    assert payload["error"] is None
    assert payload["record_id"] == "rec_1"


def test_phase_c_poller_runs_periodic_scan() -> None:
    class StubService:
        def __init__(self) -> None:
            self.calls = 0

        async def scan_once_all_tables(self) -> dict[str, Any]:
            self.calls += 1
            return {"status": "ok", "calls": self.calls}

    async def runner() -> int:
        service = StubService()
        poller = AutomationPoller(service=service, enabled=True, interval_seconds=0.1)
        await poller.start()
        await asyncio.sleep(0.28)
        await poller.stop()
        return service.calls

    calls = asyncio.run(runner())
    assert calls >= 2
