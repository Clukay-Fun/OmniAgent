from pathlib import Path
import json
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.feishu.protocol.event_adapter import FeishuEventAdapter
from src.api.automation.automation_consumer import (
    AutomationConsumer,
    AutomationStartupGate,
    InMemoryAutomationQueue,
    QueueAutomationEnqueuer,
)
from src.api.automation.automation_rules import AutomationRule
from src.api.core.event_router import FeishuEventRouter
import src.api.automation.automation_consumer as automation_consumer_module


def _build_record_changed_envelope() -> object:
    payload = {
        "header": {
            "event_type": "drive.file.bitable_record_changed_v1",
            "event_id": "evt_pipeline_1",
        },
        "event": {
            "app_token": "app_test_1",
            "table_id": "tbl_test_1",
            "record_id": "rec_test_1",
            "occurred_at": "1700000000",
            "changed_fields": {
                "状态": {
                    "old": "待处理",
                    "new": "完成",
                }
            },
        },
    }
    return FeishuEventAdapter.from_webhook_payload(payload)


def test_record_changed_pipeline_writes_run_log_jsonl(tmp_path: Path) -> None:
    run_log_path = tmp_path / "run_logs.jsonl"
    queue = InMemoryAutomationQueue()
    consumer = AutomationConsumer(
        run_log_path=run_log_path,
        startup_gate=AutomationStartupGate(startup_mode="auto", baseline_ready=True),
    )
    enqueuer = QueueAutomationEnqueuer(queue=queue, consumer=consumer)
    router = FeishuEventRouter(
        enabled_types=["drive.file.bitable_record_changed_v1"],
        automation_enqueuer=enqueuer,
    )

    result = router.route(_build_record_changed_envelope())

    assert result.status == "handled"
    assert run_log_path.exists()
    lines = run_log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event_id"] == "evt_pipeline_1"
    assert record["event_type"] == "drive.file.bitable_record_changed_v1"
    assert record["record_id"] == "rec_test_1"
    assert record["status"] == "consumed"


def test_consumer_records_metrics_and_logs(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    metric_calls: list[tuple[str, str]] = []

    def _record_automation_consumed(event_type: str, status: str) -> None:
        metric_calls.append((event_type, status))

    monkeypatch.setattr(automation_consumer_module, "record_automation_consumed", _record_automation_consumed)

    queue = InMemoryAutomationQueue()
    consumer = AutomationConsumer(
        run_log_path=tmp_path / "run_logs.jsonl",
        startup_gate=AutomationStartupGate(startup_mode="auto", baseline_ready=True),
    )
    enqueuer = QueueAutomationEnqueuer(queue=queue, consumer=consumer)

    with caplog.at_level("INFO"):
        assert enqueuer.enqueue_record_changed(
            {
                "event_id": "evt_metric_1",
                "event_type": "drive.file.bitable_record_changed_v1",
                "table_id": "tbl_metric_1",
                "record_id": "rec_metric_1",
            }
        )

    assert metric_calls == [("drive.file.bitable_record_changed_v1", "consumed")]
    assert "automation consume logged" in caplog.text


def test_startup_protection_blocks_consumption_in_auto_baseline_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    metric_calls: list[tuple[str, str]] = []

    def _record_automation_consumed(event_type: str, status: str) -> None:
        metric_calls.append((event_type, status))

    monkeypatch.setattr(automation_consumer_module, "record_automation_consumed", _record_automation_consumed)

    queue = InMemoryAutomationQueue()
    run_log_path = tmp_path / "run_logs.jsonl"
    consumer = AutomationConsumer(
        run_log_path=run_log_path,
        startup_gate=AutomationStartupGate(startup_mode="auto", baseline_ready=False),
    )
    enqueuer = QueueAutomationEnqueuer(queue=queue, consumer=consumer)

    assert enqueuer.enqueue_record_changed(
        {
            "event_id": "evt_blocked_1",
            "event_type": "drive.file.bitable_record_changed_v1",
            "table_id": "tbl_blocked_1",
            "record_id": "rec_blocked_1",
        }
    )

    assert not run_log_path.exists()
    assert queue.size() == 1
    assert metric_calls == [("drive.file.bitable_record_changed_v1", "blocked_startup_protection")]


def test_consumer_rule_failures_do_not_block_consumption(tmp_path: Path) -> None:
    run_log_path = tmp_path / "run_logs.jsonl"

    class _RaisingExecutor:
        def execute_rule(self, rule: AutomationRule, payload: dict[str, object]) -> dict[str, object]:
            _ = (rule, payload)
            raise RuntimeError("intentional failure")

    consumer = AutomationConsumer(
        run_log_path=run_log_path,
        startup_gate=AutomationStartupGate(startup_mode="auto", baseline_ready=True),
        rule_set=[
            AutomationRule(
                rule_id="r_fail",
                source_table="tbl_1",
                watched_fields=set(),
                condition_mode="all",
                conditions=[],
                actions=[{"type": "log.write"}],
            )
        ],
        action_executor=_RaisingExecutor(),
    )

    status = consumer._consume_single(
        {
            "event_id": "evt_non_block_1",
            "event_type": "drive.file.bitable_record_changed_v1",
            "table_id": "tbl_1",
            "record_id": "rec_1",
        }
    )

    assert status == "consumed"
    assert run_log_path.exists()
