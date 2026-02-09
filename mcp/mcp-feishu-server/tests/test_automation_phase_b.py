from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.automation.rules import RuleMatcher
from src.automation.service import AutomationService
from src.config import Settings


class FakeFeishuClient:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.update_calls: list[dict[str, Any]] = []
        self.calendar_calls: list[dict[str, Any]] = []
        self._update_call_count = 0
        self.fail_on_update_call: int | None = None

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
            self._update_call_count += 1
            if self.fail_on_update_call and self._update_call_count == self.fail_on_update_call:
                raise RuntimeError("mock bitable update failed")

            record_id = path.rsplit("/", 1)[-1]
            fields = (json_body or {}).get("fields") or {}
            if not isinstance(fields, dict):
                fields = {}
            self.records.setdefault(record_id, {}).update(fields)
            self.update_calls.append(
                {
                    "record_id": record_id,
                    "fields": dict(fields),
                }
            )
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

        if method == "POST" and "/calendar/v4/calendars/" in path and path.endswith("/events"):
            body = json_body or {}
            self.calendar_calls.append(dict(body))
            return {
                "code": 0,
                "data": {
                    "event": {
                        "event_id": "evt_calendar_1",
                        "url": "https://example.com/calendar/event/evt_calendar_1",
                    }
                },
            }

        raise RuntimeError(f"unexpected request: {method} {path}")


def _build_settings(tmp_path: Path, rules_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "bitable": {
                "default_app_token": "app_test",
                "default_table_id": "tbl_test",
            },
            "calendar": {
                "default_calendar_id": "cal_test",
                "timezone": "Asia/Shanghai",
                "default_duration_minutes": 30,
            },
            "automation": {
                "enabled": True,
                "verification_token": "token_test",
                "storage_dir": str(tmp_path / "automation_data"),
                "rules_file": str(rules_path),
                "action_max_retries": 0,
                "dead_letter_file": str(tmp_path / "automation_data" / "dead_letters.jsonl"),
            },
        }
    )


def _write_rules(path: Path, rules: list[dict[str, Any]]) -> None:
    payload = {
        "meta": {"version": "1.1", "status": "test"},
        "rules": rules,
    }
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_rule_match_supports_conditions() -> None:
    matcher = RuleMatcher()
    rule = {
        "trigger": {
            "field": "案件分类",
            "condition": {
                "changed": True,
                "equals": "劳动争议",
                "in": ["劳动争议", "民商事"],
                "old_not_equals_new": True,
            },
        }
    }

    old_fields = {"案件分类": "民商事"}
    current_fields = {"案件分类": "劳动争议"}
    diff = {
        "changed": {
            "案件分类": {
                "old": "民商事",
                "new": "劳动争议",
            }
        }
    }
    assert matcher.match(rule, old_fields, current_fields, diff) is True


def test_rule_match_any_field_changed_with_exclude() -> None:
    matcher = RuleMatcher()
    rule = {
        "trigger": {
            "any_field_changed": True,
            "exclude_fields": ["自动化_执行状态", "自动化_最近错误"],
        }
    }

    old_fields = {"自动化_执行状态": "处理中"}
    current_fields = {"自动化_执行状态": "成功"}
    diff = {
        "changed": {
            "自动化_执行状态": {
                "old": "处理中",
                "new": "成功",
            }
        }
    }
    assert matcher.match(rule, old_fields, current_fields, diff) is False


def test_phase_b_pipeline_writes_processing_and_success(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    _write_rules(
        rules_path,
        [
            {
                "rule_id": "R001",
                "name": "案件分类变更",
                "enabled": True,
                "priority": 100,
                "table": {"table_id": "tbl_test"},
                "trigger": {
                    "field": "案件分类",
                    "condition": {"changed": True, "equals": "劳动争议"},
                },
                "pipeline": {
                    "before_actions": [
                        {
                            "type": "bitable.update",
                            "fields": {"自动化_执行状态": "处理中", "自动化_最近错误": ""},
                        }
                    ],
                    "actions": [
                        {"type": "log.write", "level": "info", "message": "规则命中：{record_id}"},
                    ],
                    "success_actions": [
                        {
                            "type": "bitable.update",
                            "fields": {"自动化_执行状态": "成功", "自动化_最近错误": ""},
                        }
                    ],
                    "error_actions": [
                        {
                            "type": "bitable.update",
                            "fields": {"自动化_执行状态": "失败", "自动化_最近错误": "{error}"},
                        }
                    ],
                },
            }
        ],
    )

    settings = _build_settings(tmp_path, rules_path)
    client = FakeFeishuClient()
    service = AutomationService(settings=settings, client=client)

    client.records["rec_1"] = {"案件分类": "民商事"}
    init_payload = {
        "header": {
            "event_id": "evt_1",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {"app_token": "app_test", "table_id": "tbl_test", "record_id": "rec_1"},
    }
    assert asyncio.run(service.handle_event(init_payload))["kind"] == "initialized"

    client.records["rec_1"] = {"案件分类": "劳动争议"}
    event_payload = {
        "header": {
            "event_id": "evt_2",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {"app_token": "app_test", "table_id": "tbl_test", "record_id": "rec_1"},
    }

    result = asyncio.run(service.handle_event(event_payload))
    assert result["kind"] == "changed"
    assert result["rules"]["status"] == "success"
    assert result["rules"]["matched"] == 1
    assert client.records["rec_1"]["自动化_执行状态"] == "成功"
    assert client.records["rec_1"]["自动化_最近错误"] == ""
    assert len(client.update_calls) == 2


def test_phase_b_pipeline_writes_failed_status_on_action_error(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    _write_rules(
        rules_path,
        [
            {
                "rule_id": "R002",
                "name": "动作失败回写",
                "enabled": True,
                "priority": 100,
                "table": {"table_id": "tbl_test"},
                "trigger": {
                    "field": "案件分类",
                    "condition": {"changed": True, "equals": "劳动争议"},
                },
                "pipeline": {
                    "before_actions": [
                        {
                            "type": "bitable.update",
                            "fields": {"自动化_执行状态": "处理中", "自动化_最近错误": ""},
                        }
                    ],
                    "actions": [
                        {
                            "type": "bitable.update",
                            "fields": {"动作字段": "会失败"},
                        }
                    ],
                    "error_actions": [
                        {
                            "type": "bitable.update",
                            "fields": {"自动化_执行状态": "失败", "自动化_最近错误": "{error}"},
                        }
                    ],
                },
            }
        ],
    )

    settings = _build_settings(tmp_path, rules_path)
    client = FakeFeishuClient()
    client.fail_on_update_call = 2
    service = AutomationService(settings=settings, client=client)

    client.records["rec_1"] = {"案件分类": "民商事"}
    init_payload = {
        "header": {
            "event_id": "evt_11",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {"app_token": "app_test", "table_id": "tbl_test", "record_id": "rec_1"},
    }
    assert asyncio.run(service.handle_event(init_payload))["kind"] == "initialized"

    client.records["rec_1"] = {"案件分类": "劳动争议"}
    event_payload = {
        "header": {
            "event_id": "evt_12",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {"app_token": "app_test", "table_id": "tbl_test", "record_id": "rec_1"},
    }

    result = asyncio.run(service.handle_event(event_payload))
    assert result["kind"] == "changed"
    assert result["rules"]["status"] == "failed"
    assert result["rules"]["failed"] == 1
    assert client.records["rec_1"]["自动化_执行状态"] == "失败"
    assert "mock bitable update failed" in client.records["rec_1"]["自动化_最近错误"]
    assert len(client.update_calls) == 2


def test_phase_b_default_status_actions_when_pipeline_omits_them(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    _write_rules(
        rules_path,
        [
            {
                "rule_id": "R003",
                "name": "默认状态回写",
                "enabled": True,
                "priority": 100,
                "table": {"table_id": "tbl_test"},
                "trigger": {
                    "field": "案件分类",
                    "condition": {"changed": True, "equals": "劳动争议"},
                },
                "pipeline": {
                    "actions": [
                        {"type": "log.write", "level": "info", "message": "默认状态：{record_id}"},
                    ]
                },
            }
        ],
    )

    settings = _build_settings(tmp_path, rules_path)
    client = FakeFeishuClient()
    service = AutomationService(settings=settings, client=client)

    client.records["rec_1"] = {"案件分类": "民商事"}
    init_payload = {
        "header": {
            "event_id": "evt_21",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {"app_token": "app_test", "table_id": "tbl_test", "record_id": "rec_1"},
    }
    assert asyncio.run(service.handle_event(init_payload))["kind"] == "initialized"

    client.records["rec_1"] = {"案件分类": "劳动争议"}
    event_payload = {
        "header": {
            "event_id": "evt_22",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {"app_token": "app_test", "table_id": "tbl_test", "record_id": "rec_1"},
    }

    result = asyncio.run(service.handle_event(event_payload))
    assert result["rules"]["status"] == "success"
    assert client.records["rec_1"]["自动化_执行状态"] == "成功"


def test_phase_b_supports_calendar_create_action(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    _write_rules(
        rules_path,
        [
            {
                "rule_id": "R004",
                "name": "日历动作",
                "enabled": True,
                "priority": 100,
                "table": {"table_id": "tbl_test"},
                "trigger": {
                    "field": "案件分类",
                    "condition": {"changed": True, "equals": "劳动争议"},
                },
                "pipeline": {
                    "actions": [
                        {
                            "type": "calendar.create",
                            "summary_template": "劳动争议跟进：{record_id}",
                            "description_template": "委托人：{委托人}",
                            "start_field": "开庭日",
                            "duration_minutes": 45,
                        }
                    ]
                },
            }
        ],
    )

    settings = _build_settings(tmp_path, rules_path)
    client = FakeFeishuClient()
    service = AutomationService(settings=settings, client=client)

    client.records["rec_1"] = {"案件分类": "民商事", "开庭日": "2026-02-10 10:00", "委托人": "张三"}
    init_payload = {
        "header": {
            "event_id": "evt_31",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {"app_token": "app_test", "table_id": "tbl_test", "record_id": "rec_1"},
    }
    assert asyncio.run(service.handle_event(init_payload))["kind"] == "initialized"

    client.records["rec_1"] = {"案件分类": "劳动争议", "开庭日": "2026-02-10 10:00", "委托人": "张三"}
    event_payload = {
        "header": {
            "event_id": "evt_32",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {"app_token": "app_test", "table_id": "tbl_test", "record_id": "rec_1"},
    }

    result = asyncio.run(service.handle_event(event_payload))
    assert result["rules"]["status"] == "success"
    assert len(client.calendar_calls) == 1
    assert client.calendar_calls[0]["summary"] == "劳动争议跟进：rec_1"
    assert client.records["rec_1"]["自动化_执行状态"] == "成功"


@pytest.mark.parametrize(
    "new_value, expected",
    [
        ("劳动争议", True),
        ("刑事", False),
    ],
)
def test_rule_match_in_condition(new_value: str, expected: bool) -> None:
    matcher = RuleMatcher()
    rule = {
        "trigger": {
            "field": "案件分类",
            "condition": {
                "in": ["劳动争议", "民商事"],
            },
        }
    }
    old_fields = {"案件分类": "民商事"}
    current_fields = {"案件分类": new_value}
    diff = {
        "changed": {
            "案件分类": {
                "old": "民商事",
                "new": new_value,
            }
        }
    }
    assert matcher.match(rule, old_fields, current_fields, diff) is expected
