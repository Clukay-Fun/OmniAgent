from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.automation.rules import RuleMatcher, RuleStore
from src.automation.service import AutomationService
from src.config import Settings


class FakeFeishuClient:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.target_records: dict[str, dict[str, dict[str, Any]]] = {}
        self.update_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.calendar_calls: list[dict[str, Any]] = []
        self.get_params_calls: list[dict[str, Any] | None] = []
        self.search_payloads: list[dict[str, Any] | None] = []
        self._update_call_count = 0
        self.fail_on_update_call: int | None = None
        self._next_created_id = 1

    @staticmethod
    def _extract_table_id(path: str) -> str:
        marker = "/tables/"
        if marker not in path:
            return ""
        tail = path.split(marker, 1)[1]
        return tail.split("/", 1)[0]

    def _bucket_for_table(self, table_id: str) -> dict[str, dict[str, Any]]:
        if table_id == "tbl_test":
            return self.records
        return self.target_records.setdefault(table_id, {})

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        _ = headers
        table_id = self._extract_table_id(path)
        bucket = self._bucket_for_table(table_id)

        if method == "GET" and "/records/" in path:
            record_id = path.rsplit("/", 1)[-1]
            self.get_params_calls.append(dict(params) if isinstance(params, dict) else None)
            fields = dict(bucket.get(record_id, {}))
            if isinstance(params, dict) and params.get("field_names"):
                try:
                    names = json.loads(str(params.get("field_names")))
                except json.JSONDecodeError:
                    names = []
                if isinstance(names, list) and names:
                    fields = {
                        key: value
                        for key, value in fields.items()
                        if key in {str(name) for name in names}
                    }
            return {
                "code": 0,
                "data": {
                    "record": {
                        "record_id": record_id,
                        "fields": fields,
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
            bucket.setdefault(record_id, {}).update(fields)
            self.update_calls.append(
                {
                    "table_id": table_id,
                    "record_id": record_id,
                    "fields": dict(fields),
                }
            )
            return {
                "code": 0,
                "data": {
                    "record": {
                        "record_id": record_id,
                        "fields": bucket.get(record_id, {}),
                    }
                },
            }

        if method == "POST" and path.endswith("/records/search"):
            self.search_payloads.append(dict(json_body) if isinstance(json_body, dict) else None)
            field_names = (json_body or {}).get("field_names")
            allowed = set([str(name) for name in field_names]) if isinstance(field_names, list) else set()

            items: list[dict[str, Any]] = []
            for record_id, fields in bucket.items():
                out_fields = dict(fields)
                if allowed:
                    out_fields = {k: v for k, v in out_fields.items() if k in allowed}
                items.append(
                    {
                        "record_id": record_id,
                        "fields": out_fields,
                        "last_modified_time": 1739000000000,
                    }
                )

            return {
                "code": 0,
                "data": {"items": items, "has_more": False, "page_token": ""},
            }

        if method == "POST" and path.endswith("/records"):
            fields = (json_body or {}).get("fields") or {}
            if not isinstance(fields, dict):
                fields = {}
            record_id = f"rec_created_{self._next_created_id}"
            self._next_created_id += 1
            bucket[record_id] = dict(fields)
            self.create_calls.append(
                {
                    "table_id": table_id,
                    "record_id": record_id,
                    "fields": dict(fields),
                }
            )
            return {
                "code": 0,
                "data": {
                    "record": {
                        "record_id": record_id,
                        "fields": dict(fields),
                    }
                },
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


def _build_settings(
    tmp_path: Path,
    rules_path: Path,
    *,
    status_write_enabled: bool = True,
    automation_overrides: dict[str, Any] | None = None,
) -> Settings:
    automation_payload: dict[str, Any] = {
        "enabled": True,
        "verification_token": "token_test",
        "storage_dir": str(tmp_path / "automation_data"),
        "rules_file": str(rules_path),
        "action_max_retries": 0,
        "dead_letter_file": str(tmp_path / "automation_data" / "dead_letters.jsonl"),
        "run_log_file": str(tmp_path / "automation_data" / "run_logs.jsonl"),
        "status_write_enabled": status_write_enabled,
    }
    if automation_overrides:
        automation_payload.update(automation_overrides)

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
            "automation": automation_payload,
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


def test_rule_match_supports_trigger_all() -> None:
    matcher = RuleMatcher()
    rule = {
        "trigger": {
            "all": [
                {
                    "field": "协作类型",
                    "condition": {"in": ["需要协助", "分派给他人", "团队共享"]},
                },
                {
                    "field": "已同步到总览",
                    "condition": {"equals": False},
                },
            ]
        }
    }
    old_fields = {"协作类型": "仅自己", "已同步到总览": False}
    current_fields = {"协作类型": "需要协助", "已同步到总览": False}
    diff = {
        "changed": {
            "协作类型": {
                "old": "仅自己",
                "new": "需要协助",
            }
        }
    }
    assert matcher.match(rule, old_fields, current_fields, diff) is True


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


def test_phase_b_supports_bitable_upsert_with_create_and_update(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    _write_rules(
        rules_path,
        [
            {
                "rule_id": "R100",
                "name": "工作台首次同步",
                "enabled": True,
                "priority": 100,
                "table": {"table_id": "tbl_test"},
                "trigger": {
                    "all": [
                        {
                            "field": "协作类型",
                            "condition": {"in": ["需要协助", "分派给他人", "团队共享"]},
                        },
                        {
                            "field": "已同步到总览",
                            "condition": {"equals": False},
                        },
                    ]
                },
                "pipeline": {
                    "actions": [
                        {
                            "type": "bitable.upsert",
                            "target_table_id": "tbl_overview",
                            "match_fields": {
                                "任务ID": "{record_id}",
                                "发起人": "{发起人}",
                            },
                            "update_fields": {
                                "任务ID": "{record_id}",
                                "待办事项": "{待办事项}",
                                "任务状态": "{任务状态}",
                            },
                            "create_fields": {
                                "来源表": "{table_id}",
                            },
                        },
                        {
                            "type": "bitable.update",
                            "fields": {
                                "已同步到总览": True,
                                "任务ID": "{record_id}",
                            },
                        },
                    ]
                },
            },
            {
                "rule_id": "R101",
                "name": "工作台后续更新同步",
                "enabled": True,
                "priority": 90,
                "table": {"table_id": "tbl_test"},
                "trigger": {
                    "all": [
                        {
                            "any_field_changed": True,
                            "exclude_fields": ["已同步到总览", "任务ID"],
                        },
                        {
                            "field": "已同步到总览",
                            "condition": {"equals": True},
                        },
                        {
                            "field": "协作类型",
                            "condition": {"in": ["需要协助", "分派给他人", "团队共享"]},
                        },
                    ]
                },
                "pipeline": {
                    "actions": [
                        {
                            "type": "bitable.upsert",
                            "target_table_id": "tbl_overview",
                            "match_fields": {
                                "任务ID": "{record_id}",
                                "发起人": "{发起人}",
                            },
                            "update_fields": {
                                "任务ID": "{record_id}",
                                "待办事项": "{待办事项}",
                                "任务状态": "{任务状态}",
                            },
                        }
                    ]
                },
            },
        ],
    )

    settings = _build_settings(
        tmp_path,
        rules_path,
        status_write_enabled=False,
        automation_overrides={
            "trigger_on_new_record_event": True,
        },
    )
    client = FakeFeishuClient()
    service = AutomationService(settings=settings, client=client)

    initiator = [{"id": "ou_test_user_1", "name": "房怡康"}]

    client.records["rec_1"] = {
        "发起人": initiator,
        "待办事项": "首次创建",
        "任务状态": "待开始",
        "协作类型": "需要协助",
        "已同步到总览": False,
    }
    first_payload = {
        "header": {
            "event_id": "evt_u_1",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {"app_token": "app_test", "table_id": "tbl_test", "record_id": "rec_1"},
    }
    first_result = asyncio.run(service.handle_event(first_payload))
    assert first_result["kind"] == "initialized_triggered"
    assert first_result["rules"]["status"] == "success"
    assert client.create_calls
    assert client.records["rec_1"]["已同步到总览"] is True
    assert client.records["rec_1"]["任务ID"] == "rec_1"

    created_id = client.create_calls[0]["record_id"]
    overview_bucket = client.target_records.get("tbl_overview") or {}
    assert created_id in overview_bucket
    assert overview_bucket[created_id]["任务ID"] == "rec_1"
    assert overview_bucket[created_id]["待办事项"] == "首次创建"
    assert overview_bucket[created_id]["发起人"] == [{"id": "ou_test_user_1"}]

    client.records["rec_1"]["待办事项"] = "二次更新"
    second_payload = {
        "header": {
            "event_id": "evt_u_2",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {"app_token": "app_test", "table_id": "tbl_test", "record_id": "rec_1"},
    }
    second_result = asyncio.run(service.handle_event(second_payload))
    assert second_result["kind"] == "changed"
    assert second_result["rules"]["status"] == "success"
    assert len(client.create_calls) == 1
    assert overview_bucket[created_id]["待办事项"] == "二次更新"


def test_phase_b_upsert_can_update_all_duplicate_matches(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    _write_rules(
        rules_path,
        [
            {
                "rule_id": "R102",
                "name": "更新重复匹配记录",
                "enabled": True,
                "priority": 100,
                "table": {"table_id": "tbl_test"},
                "trigger": {
                    "field": "协作类型",
                    "condition": {"equals": "需要协助"},
                },
                "pipeline": {
                    "actions": [
                        {
                            "type": "bitable.upsert",
                            "target_table_id": "tbl_overview",
                            "update_all_matches": True,
                            "match_fields": {
                                "源记录ID": "{record_id}",
                            },
                            "update_fields": {
                                "任务描述": "{待办事项}",
                            },
                        }
                    ]
                },
            }
        ],
    )

    settings = _build_settings(
        tmp_path,
        rules_path,
        status_write_enabled=False,
        automation_overrides={
            "trigger_on_new_record_event": True,
        },
    )
    client = FakeFeishuClient()
    service = AutomationService(settings=settings, client=client)

    client.target_records["tbl_overview"] = {
        "rec_dup_1": {
            "源记录ID": {
                "type": 1,
                "value": [{"text": "rec_1", "type": "text"}],
            },
            "任务描述": "{任务描述}",
        },
        "rec_dup_2": {"源记录ID": "rec_1", "任务描述": "旧值"},
    }

    client.records["rec_1"] = {
        "协作类型": "需要协助",
        "待办事项": "最新内容",
    }

    payload = {
        "header": {
            "event_id": "evt_dup_1",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {"app_token": "app_test", "table_id": "tbl_test", "record_id": "rec_1"},
    }

    result = asyncio.run(service.handle_event(payload))
    assert result["kind"] == "initialized_triggered"
    assert result["rules"]["status"] == "success"

    overview = client.target_records["tbl_overview"]
    assert overview["rec_dup_1"]["任务描述"] == "最新内容"
    assert overview["rec_dup_2"]["任务描述"] == "最新内容"
    assert len(client.create_calls) == 0


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


def test_rule_store_watch_plan_extracts_trigger_fields(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    _write_rules(
        rules_path,
        [
            {
                "rule_id": "R005",
                "enabled": True,
                "priority": 10,
                "table": {"table_id": "tbl_test"},
                "trigger": {
                    "field": "案件分类",
                    "condition": {"changed": True},
                },
            },
            {
                "rule_id": "R006",
                "enabled": True,
                "priority": 9,
                "table": {"table_id": "tbl_test"},
                "trigger": {
                    "field": "开庭日",
                    "condition": {"changed": True},
                },
            },
        ],
    )

    store = RuleStore(rules_path)
    plan = store.get_watch_plan("tbl_test")
    assert plan["mode"] == "fields"
    assert plan["fields"] == ["开庭日", "案件分类"]


def test_rule_store_watch_plan_falls_back_to_full_for_any_field_changed(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    _write_rules(
        rules_path,
        [
            {
                "rule_id": "R_ANY",
                "enabled": True,
                "priority": 10,
                "table": {"table_id": "tbl_test"},
                "trigger": {
                    "any_field_changed": True,
                    "exclude_fields": ["自动化_执行状态"],
                },
            }
        ],
    )

    store = RuleStore(rules_path)
    plan = store.get_watch_plan("tbl_test")
    assert plan["mode"] == "full"
    assert plan["fields"] == []


def test_status_write_disabled_skips_status_update_actions(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    _write_rules(
        rules_path,
        [
            {
                "rule_id": "R007",
                "name": "仅状态回写",
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
                        {"type": "log.write", "message": "仅写日志"},
                    ],
                    "success_actions": [
                        {
                            "type": "bitable.update",
                            "fields": {"自动化_执行状态": "成功", "自动化_最近错误": ""},
                        }
                    ],
                },
            }
        ],
    )

    settings = _build_settings(tmp_path, rules_path, status_write_enabled=False)
    client = FakeFeishuClient()
    service = AutomationService(settings=settings, client=client)

    client.records["rec_1"] = {"案件分类": "民商事"}
    assert asyncio.run(service.handle_event({
        "header": {
            "event_id": "evt_s1",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {"app_token": "app_test", "table_id": "tbl_test", "record_id": "rec_1"},
    }))["kind"] == "initialized"

    client.records["rec_1"] = {"案件分类": "劳动争议"}
    result = asyncio.run(service.handle_event({
        "header": {
            "event_id": "evt_s2",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {"app_token": "app_test", "table_id": "tbl_test", "record_id": "rec_1"},
    }))

    assert result["rules"]["status"] == "success"
    assert len(client.update_calls) == 0


def test_event_fetch_uses_watched_field_names(tmp_path: Path) -> None:
    rules_path = tmp_path / "rules.yaml"
    _write_rules(
        rules_path,
        [
            {
                "rule_id": "R008",
                "name": "只关心案件分类",
                "enabled": True,
                "priority": 100,
                "table": {"table_id": "tbl_test"},
                "trigger": {
                    "field": "案件分类",
                    "condition": {"changed": True, "equals": "劳动争议"},
                },
                "pipeline": {
                    "actions": [{"type": "log.write", "message": "hit"}],
                },
            }
        ],
    )

    settings = _build_settings(tmp_path, rules_path, status_write_enabled=False)
    client = FakeFeishuClient()
    service = AutomationService(settings=settings, client=client)

    client.records["rec_1"] = {"案件分类": "民商事", "开庭日": "2026-02-10", "案号": "A-1"}
    assert asyncio.run(service.handle_event({
        "header": {
            "event_id": "evt_w1",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {"app_token": "app_test", "table_id": "tbl_test", "record_id": "rec_1"},
    }))["kind"] == "initialized"

    client.records["rec_1"] = {"案件分类": "民商事", "开庭日": "2026-02-11", "案号": "A-1"}
    result = asyncio.run(service.handle_event({
        "header": {
            "event_id": "evt_w2",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {"app_token": "app_test", "table_id": "tbl_test", "record_id": "rec_1"},
    }))

    assert result["kind"] == "no_change"
    assert client.get_params_calls
    last_params = client.get_params_calls[-1] or {}
    field_names = json.loads(str(last_params.get("field_names") or "[]"))
    assert "案件分类" in field_names
