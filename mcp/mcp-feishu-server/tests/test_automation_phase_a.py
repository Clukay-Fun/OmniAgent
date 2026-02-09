from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from src.automation.service import AutomationService
from src.automation.snapshot import SnapshotStore
from src.automation.store import IdempotencyStore
from src.config import Settings


class FakeFeishuClient:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        _ = params, json_body, headers
        if method == "GET" and "/records/" in path:
            record_id = path.rsplit("/", 1)[-1]
            fields = self.records.get(record_id) or {}
            return {
                "code": 0,
                "data": {
                    "record": {
                        "record_id": record_id,
                        "fields": fields,
                    }
                },
            }

        if method == "POST" and path.endswith("/records/search"):
            items = []
            for record_id, fields in self.records.items():
                items.append(
                    {
                        "record_id": record_id,
                        "fields": fields,
                        "last_modified_time": 1739000000000,
                    }
                )
            return {
                "code": 0,
                "data": {
                    "items": items,
                    "has_more": False,
                    "page_token": "",
                },
            }

        raise RuntimeError(f"unexpected request: {method} {path}")


def _build_settings(tmp_path: Path) -> Settings:
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
                "event_ttl_seconds": 3600,
                "business_ttl_seconds": 3600,
                "max_dedupe_keys": 1000,
            },
        }
    )


def test_snapshot_diff_tracks_field_changes(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "snapshot.json")
    old_fields = {
        "案件分类": "民商事",
        "金额": 100,
        "标签": ["A", "B"],
    }
    new_fields = {
        "案件分类": "劳动争议",
        "金额": 100,
        "标签": ["A", "B"],
        "新字段": "x",
    }

    diff = store.diff(old_fields, new_fields)
    assert diff["has_changes"] is True
    assert "案件分类" in diff["changed"]
    assert "新字段" in diff["changed"]
    assert "金额" not in diff["changed"]


def test_snapshot_init_and_load(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "snapshot.json")
    count = store.init_full_snapshot(
        "tbl1",
        {
            "rec1": {"字段A": 1},
            "rec2": {"字段B": 2},
        },
    )
    assert count == 2
    assert store.load("tbl1", "rec1") == {"字段A": 1}
    assert store.load("tbl1", "rec2") == {"字段B": 2}


def test_idempotency_store_dedup(tmp_path: Path) -> None:
    dedupe = IdempotencyStore(
        tmp_path / "idempotency.json",
        event_ttl_seconds=3600,
        business_ttl_seconds=3600,
        max_keys=1000,
    )

    assert dedupe.is_event_duplicate("evt_1") is False
    dedupe.mark_event("evt_1")
    assert dedupe.is_event_duplicate("evt_1") is True

    assert dedupe.is_business_duplicate("biz_1") is False
    dedupe.mark_business("biz_1")
    assert dedupe.is_business_duplicate("biz_1") is True


def test_automation_service_event_flow_phase_a(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    fake_client = FakeFeishuClient()
    service = AutomationService(settings=settings, client=fake_client)

    fake_client.records["rec_1"] = {"案件分类": "民商事"}
    first_payload = {
        "header": {
            "event_id": "evt_1",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {
            "app_token": "app_test",
            "table_id": "tbl_test",
            "record_id": "rec_1",
        },
    }
    first_result = asyncio.run(service.handle_event(first_payload))
    assert first_result["kind"] == "initialized"

    fake_client.records["rec_1"] = {"案件分类": "劳动争议"}
    second_payload = {
        "header": {
            "event_id": "evt_2",
            "event_type": "drive.file.bitable_record_changed_v1",
            "token": "token_test",
        },
        "event": {
            "app_token": "app_test",
            "table_id": "tbl_test",
            "record_id": "rec_1",
        },
    }
    second_result = asyncio.run(service.handle_event(second_payload))
    assert second_result["kind"] == "changed"
    assert "案件分类" in second_result["changed_fields"]

    duplicate_result = asyncio.run(service.handle_event(second_payload))
    assert duplicate_result["kind"] == "duplicate_event"


def test_automation_service_url_verification(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = AutomationService(settings=settings, client=FakeFeishuClient())

    payload = {
        "type": "url_verification",
        "token": "token_test",
        "challenge": "abc123",
    }
    result = asyncio.run(service.handle_event(payload))
    assert result == {"kind": "challenge", "challenge": "abc123"}
