from pathlib import Path
import sys
import pytest


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.feishu.protocol.event_adapter import EventEnvelope, FeishuEventAdapter, MessageEvent
from src.api.core.event_router import FeishuEventRouter
from src.core.capabilities.skills.bitable.schema_cache import SchemaCache
import src.api.core.event_router as event_router_module


def _build_message_event(event_type: str = "im.message.receive_v1") -> MessageEvent:
    return MessageEvent(
        event_type=event_type,
        event_id="evt_1",
        message_id="msg_1",
        chat_id="oc_1",
        chat_type="p2p",
        message_type="text",
        content='{"text":"hello"}',
        sender_open_id="ou_1",
        sender_user_id="u_1",
        sender_type="user",
    )


def test_event_router_accepts_message_event() -> None:
    router = FeishuEventRouter(enabled_types=["im.message.receive_v1"])
    envelope = EventEnvelope(event_type="im.message.receive_v1", event_id="evt_1", message=_build_message_event())

    result = router.route(envelope)

    assert result.status == "accepted"
    assert result.reason == "message"


def test_event_router_ignores_unimplemented_event() -> None:
    router = FeishuEventRouter(enabled_types=["drive.file.unknown_event_v1"])
    envelope = EventEnvelope(event_type="drive.file.unknown_event_v1", event_id="evt_2", message=None)

    result = router.route(envelope)

    assert result.status == "ignored"
    assert result.reason == "event_not_implemented"


def test_event_router_handles_record_changed_and_enqueues_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    metric_calls: list[tuple[str, str]] = []

    class _AutomationEnqueuer:
        def enqueue_record_changed(self, event_payload: dict[str, object]) -> bool:
            calls.append(event_payload)
            return True

    def _record_automation_enqueue(event_type: str, status: str) -> None:
        metric_calls.append((event_type, status))

    monkeypatch.setattr(event_router_module, "record_automation_enqueue", _record_automation_enqueue)

    payload = {
        "header": {
            "event_type": "drive.file.bitable_record_changed_v1",
            "event_id": "evt_record_1",
        },
        "event": {
            "app_token": "app_mock_1",
            "table_id": "tbl_mock_1",
            "record_id": "rec_mock_1",
            "occurred_at": "1700000000",
            "changed_fields": {
                "案件状态": {
                    "old": "待处理",
                    "new": "已完成",
                },
                "负责人": {
                    "old": "张三",
                    "new": "李四",
                },
            },
        },
    }
    envelope = FeishuEventAdapter.from_webhook_payload(payload)
    router = FeishuEventRouter(
        enabled_types=["drive.file.bitable_record_changed_v1"],
        automation_enqueuer=_AutomationEnqueuer(),
    )

    result = router.route(envelope)

    assert result.status == "handled"
    assert result.reason == "record_changed"
    assert result.handler == "record_changed"
    assert result.payload == {
        "event_id": "evt_record_1",
        "event_type": "drive.file.bitable_record_changed_v1",
        "app_token": "app_mock_1",
        "table_id": "tbl_mock_1",
        "record_id": "rec_mock_1",
        "changed_fields": ["案件状态", "负责人"],
        "occurred_at": "1700000000",
        "automation_enqueue": "enqueued",
    }
    assert calls == [
        {
            "event_id": "evt_record_1",
            "event_type": "drive.file.bitable_record_changed_v1",
            "app_token": "app_mock_1",
            "table_id": "tbl_mock_1",
            "record_id": "rec_mock_1",
            "changed_fields": ["案件状态", "负责人"],
            "occurred_at": "1700000000",
            "raw_fragment": payload["event"],
        }
    ]
    assert metric_calls == [("drive.file.bitable_record_changed_v1", "enqueued")]


def test_record_changed_enqueue_missing_is_safe_noop_and_records_metric(monkeypatch) -> None:
    metric_calls: list[tuple[str, str]] = []

    def _record_automation_enqueue(event_type: str, status: str) -> None:
        metric_calls.append((event_type, status))

    monkeypatch.setattr(event_router_module, "record_automation_enqueue", _record_automation_enqueue)

    payload = {
        "header": {
            "event_type": "drive.file.bitable_record_changed_v1",
            "event_id": "evt_record_missing_1",
        },
        "event": {
            "app_token": "app_mock_7",
            "table_id": "tbl_mock_7",
            "record_id": "rec_mock_7",
        },
    }
    envelope = FeishuEventAdapter.from_webhook_payload(payload)
    router = FeishuEventRouter(enabled_types=["drive.file.bitable_record_changed_v1"])

    result = router.route(envelope)

    assert result.status == "handled"
    assert result.payload is not None
    assert result.payload["automation_enqueue"] == "not_available"
    assert metric_calls == [("drive.file.bitable_record_changed_v1", "not_available")]


def test_record_changed_legacy_automation_engine_hook_is_supported() -> None:
    calls: list[dict[str, object]] = []

    class _AutomationEngine:
        def on_record_changed(self, **kwargs) -> None:
            calls.append(kwargs)

    payload = {
        "header": {
            "event_type": "drive.file.bitable_record_changed_v1",
            "event_id": "evt_record_legacy_1",
        },
        "event": {
            "app_token": "app_mock_legacy",
            "table_id": "tbl_mock_legacy",
            "record_id": "rec_mock_legacy",
            "changed_fields": ["状态"],
        },
    }
    envelope = FeishuEventAdapter.from_webhook_payload(payload)
    router = FeishuEventRouter(
        enabled_types=["drive.file.bitable_record_changed_v1"],
        automation_engine=_AutomationEngine(),
    )

    result = router.route(envelope)

    assert result.status == "handled"
    assert result.payload is not None
    assert result.payload["automation_enqueue"] == "enqueued"
    assert calls == [
        {
            "event_id": "evt_record_legacy_1",
            "app_token": "app_mock_legacy",
            "table_id": "tbl_mock_legacy",
            "record_id": "rec_mock_legacy",
            "changed_fields": ["状态"],
            "raw_event": payload["event"],
        }
    ]


def test_record_changed_enqueue_exception_is_non_blocking(monkeypatch) -> None:
    metric_calls: list[tuple[str, str]] = []

    class _AutomationEnqueuer:
        def enqueue_record_changed(self, event_payload: dict[str, object]) -> bool:
            _ = event_payload
            raise RuntimeError("enqueue failed")

    def _record_automation_enqueue(event_type: str, status: str) -> None:
        metric_calls.append((event_type, status))

    monkeypatch.setattr(event_router_module, "record_automation_enqueue", _record_automation_enqueue)

    payload = {
        "header": {
            "event_type": "drive.file.bitable_record_changed_v1",
            "event_id": "evt_record_failed_1",
        },
        "event": {
            "app_token": "app_mock_8",
            "table_id": "tbl_mock_8",
            "record_id": "rec_mock_8",
        },
    }
    envelope = FeishuEventAdapter.from_webhook_payload(payload)
    router = FeishuEventRouter(
        enabled_types=["drive.file.bitable_record_changed_v1"],
        automation_enqueuer=_AutomationEnqueuer(),
    )

    result = router.route(envelope)

    assert result.status == "handled"
    assert result.payload is not None
    assert result.payload["automation_enqueue"] == "failed"
    assert metric_calls == [("drive.file.bitable_record_changed_v1", "failed")]


def test_event_router_ignores_disabled_event_type() -> None:
    router = FeishuEventRouter(enabled_types=["im.message.receive_v1"])
    envelope = EventEnvelope(event_type="calendar.calendar.changed_v4", event_id="evt_3", message=None)

    result = router.route(envelope)

    assert result.status == "ignored"
    assert result.reason == "event_type_disabled"


def test_event_router_handles_field_changed_and_calls_hook() -> None:
    calls: list[dict[str, object]] = []

    class _SchemaSync:
        def on_field_changed(self, **kwargs) -> None:
            calls.append(kwargs)

    payload = {
        "header": {
            "event_type": "drive.file.bitable_field_changed_v1",
            "event_id": "evt_field_1",
        },
        "event": {
            "app_token": "app_mock_2",
            "table_id": "tbl_mock_2",
            "field_id": "fld_mock_2",
            "field_name": "优先级",
        },
    }
    envelope = FeishuEventAdapter.from_webhook_payload(payload)
    router = FeishuEventRouter(
        enabled_types=["drive.file.bitable_field_changed_v1"],
        schema_sync=_SchemaSync(),
    )

    result = router.route(envelope)

    assert result.status == "handled"
    assert result.reason == "field_changed"
    assert result.handler == "field_changed"
    assert result.payload == {
        "event_id": "evt_field_1",
        "app_token": "app_mock_2",
        "table_id": "tbl_mock_2",
        "field_id": "fld_mock_2",
        "field_name": "优先级",
        "schema_sync_hook": "called",
    }
    assert calls == [
        {
            "event_id": "evt_field_1",
            "app_token": "app_mock_2",
            "table_id": "tbl_mock_2",
            "field_id": "fld_mock_2",
            "field_name": "优先级",
            "raw_event": payload["event"],
        }
    ]


def test_field_changed_invalidate_schema_cache_when_table_id_exists() -> None:
    invalidated_table_ids: list[str] = []

    class _SchemaCache:
        def invalidate(self, table_id: str) -> None:
            invalidated_table_ids.append(table_id)

    class _SchemaSync:
        def __init__(self) -> None:
            self.schema_cache = _SchemaCache()

    payload = {
        "header": {
            "event_type": "drive.file.bitable_field_changed_v1",
            "event_id": "evt_field_invalidate_1",
        },
        "event": {
            "app_token": "app_mock_3",
            "table_id": "tbl_invalidate_1",
            "field_id": "fld_mock_3",
            "field_name": "状态",
            "change_type": "add",
        },
    }
    envelope = FeishuEventAdapter.from_webhook_payload(payload)
    router = FeishuEventRouter(
        enabled_types=["drive.file.bitable_field_changed_v1"],
        schema_sync=_SchemaSync(),
    )

    result = router.route(envelope)

    assert result.status == "handled"
    assert invalidated_table_ids == ["tbl_invalidate_1"]


def test_field_changed_invalidate_real_schema_cache_entry() -> None:
    cache = SchemaCache()
    cache.set_schema("tbl_invalidate_real", [{"name": "金额", "type": 2}])

    class _SchemaSync:
        def __init__(self) -> None:
            self.schema_cache = cache

    payload = {
        "header": {
            "event_type": "drive.file.bitable_field_changed_v1",
            "event_id": "evt_field_invalidate_real_1",
        },
        "event": {
            "app_token": "app_mock_3",
            "table_id": "tbl_invalidate_real",
            "field_id": "fld_mock_3",
            "field_name": "金额",
            "change_type": "type_change",
        },
    }
    envelope = FeishuEventAdapter.from_webhook_payload(payload)
    router = FeishuEventRouter(
        enabled_types=["drive.file.bitable_field_changed_v1"],
        schema_sync=_SchemaSync(),
    )

    result = router.route(envelope)

    assert result.status == "handled"
    assert cache.get_schema("tbl_invalidate_real") is None


def test_field_changed_rename_emits_warning_and_alert_metric(monkeypatch, caplog) -> None:
    alerts: list[str] = []

    def _record_alert(change_type: str) -> None:
        alerts.append(change_type)

    monkeypatch.setattr(event_router_module, "record_schema_watcher_alert", _record_alert)

    class _SchemaCache:
        def invalidate(self, table_id: str) -> None:
            return None

    class _SchemaSync:
        def __init__(self) -> None:
            self.schema_cache = _SchemaCache()

    payload = {
        "header": {
            "event_type": "drive.file.bitable_field_changed_v1",
            "event_id": "evt_field_rename_1",
        },
        "event": {
            "app_token": "app_mock_4",
            "table_id": "tbl_mock_4",
            "field_id": "fld_mock_4",
            "field_name": "原字段",
            "change_type": "rename",
        },
    }
    envelope = FeishuEventAdapter.from_webhook_payload(payload)
    router = FeishuEventRouter(
        enabled_types=["drive.file.bitable_field_changed_v1"],
        schema_sync=_SchemaSync(),
    )

    with caplog.at_level("WARNING"):
        result = router.route(envelope)

    assert result.status == "handled"
    assert "field_changed alert: schema cache invalidated" in caplog.text
    assert alerts == ["rename"]


def test_field_changed_without_schema_cache_is_safe_noop() -> None:
    class _SchemaSync:
        pass

    payload = {
        "header": {
            "event_type": "drive.file.bitable_field_changed_v1",
            "event_id": "evt_field_no_cache_1",
        },
        "event": {
            "app_token": "app_mock_5",
            "table_id": "tbl_mock_5",
            "field_id": "fld_mock_5",
            "field_name": "优先级",
            "change_type": "add",
        },
    }
    envelope = FeishuEventAdapter.from_webhook_payload(payload)
    router = FeishuEventRouter(
        enabled_types=["drive.file.bitable_field_changed_v1"],
        schema_sync=_SchemaSync(),
    )

    result = router.route(envelope)

    assert result.status == "handled"
    assert result.reason == "field_changed"


def test_field_changed_missing_table_id_warns_and_counts_parse_failure(monkeypatch, caplog) -> None:
    alerts: list[str] = []

    def _record_alert(change_type: str) -> None:
        alerts.append(change_type)

    monkeypatch.setattr(event_router_module, "record_schema_watcher_alert", _record_alert)

    payload = {
        "header": {
            "event_type": "drive.file.bitable_field_changed_v1",
            "event_id": "evt_field_missing_table_1",
        },
        "event": {
            "app_token": "app_mock_6",
            "field_id": "fld_mock_6",
            "field_name": "优先级",
            "change_type": "add",
        },
    }
    envelope = FeishuEventAdapter.from_webhook_payload(payload)
    router = FeishuEventRouter(enabled_types=["drive.file.bitable_field_changed_v1"])

    with caplog.at_level("WARNING"):
        result = router.route(envelope)

    assert result.status == "handled"
    assert "field_changed missing table_id" in caplog.text
    assert alerts == ["parse_failure"]


def test_event_router_handles_calendar_changed_and_calls_hook() -> None:
    calls: list[dict[str, object]] = []

    class _ReminderEngine:
        def on_calendar_changed(self, **kwargs) -> None:
            calls.append(kwargs)

    payload = {
        "header": {
            "event_type": "calendar.calendar.event.changed_v4",
            "event_id": "evt_calendar_1",
        },
        "event": {
            "calendar_id": "cal_mock_1",
            "event_id": "event_mock_1",
            "summary": "项目例会",
        },
    }
    envelope = FeishuEventAdapter.from_webhook_payload(payload)
    router = FeishuEventRouter(
        enabled_types=["calendar.calendar.event.changed_v4"],
        reminder_engine=_ReminderEngine(),
    )

    result = router.route(envelope)

    assert result.status == "handled"
    assert result.reason == "calendar_changed"
    assert result.handler == "calendar_changed"
    assert result.payload == {
        "event_id": "evt_calendar_1",
        "calendar_id": "cal_mock_1",
        "calendar_event_id": "event_mock_1",
        "summary": "项目例会",
        "reminder_hook": "called",
    }
    assert calls == [
        {
            "event_id": "evt_calendar_1",
            "calendar_id": "cal_mock_1",
            "calendar_event_id": "event_mock_1",
            "summary": "项目例会",
            "raw_event": payload["event"],
        }
    ]


def test_event_router_calendar_prefers_enqueue_hook() -> None:
    calls: list[dict[str, object]] = []

    class _ReminderEngine:
        def enqueue_calendar_changed(self, **kwargs) -> bool:
            calls.append(kwargs)
            return True

    payload = {
        "header": {
            "event_type": "calendar.calendar.event.changed_v4",
            "event_id": "evt_calendar_enqueue_1",
        },
        "event": {
            "calendar_id": "cal_enqueue_1",
            "event_id": "event_enqueue_1",
            "summary": "例会",
        },
    }
    envelope = FeishuEventAdapter.from_webhook_payload(payload)
    router = FeishuEventRouter(
        enabled_types=["calendar.calendar.event.changed_v4"],
        reminder_engine=_ReminderEngine(),
    )

    result = router.route(envelope)

    assert result.status == "handled"
    assert result.payload is not None
    assert result.payload["reminder_hook"] == "enqueued"
    assert calls == [
        {
            "event_id": "evt_calendar_enqueue_1",
            "calendar_id": "cal_enqueue_1",
            "calendar_event_id": "event_enqueue_1",
            "summary": "例会",
            "raw_event": payload["event"],
        }
    ]


def test_event_router_handles_calendar_container_changed_without_event_id() -> None:
    payload = {
        "header": {
            "event_type": "calendar.calendar.changed_v4",
            "event_id": "evt_calendar_2",
        },
        "event": {
            "calendar": {
                "calendar_id": "cal_mock_2",
                "summary": "团队日历",
            }
        },
    }
    envelope = FeishuEventAdapter.from_webhook_payload(payload)
    router = FeishuEventRouter(enabled_types=["calendar.calendar.changed_v4"])

    result = router.route(envelope)

    assert result.status == "handled"
    assert result.reason == "calendar_changed"
    assert result.handler == "calendar_changed"
    assert result.payload == {
        "event_id": "evt_calendar_2",
        "calendar_id": "cal_mock_2",
        "calendar_event_id": "",
        "summary": "团队日历",
        "reminder_hook": "not_available",
    }
