from __future__ import annotations

from pathlib import Path
import sys


MCP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_ROOT))

from src.automation.delay_store import DelayedTask, DelayStore


def test_delayed_task_round_trip() -> None:
    task = DelayedTask(
        task_id="task-1",
        rule_id="rule-1",
        trigger_at=100.0,
        payload={"action": {"type": "log.write"}},
        status="scheduled",
        created_at=10.0,
        executed_at=None,
        error_detail=None,
    )

    restored = DelayedTask.from_dict(task.to_dict())

    assert restored == task


def test_schedule_and_get_due_tasks(tmp_path: Path) -> None:
    store = DelayStore(tmp_path / "delay_queue.jsonl")
    store.schedule(
        DelayedTask(
            task_id="task-1",
            rule_id="rule-1",
            trigger_at=150.0,
            payload={"k": "v"},
        )
    )

    due_before = store.get_due_tasks(now_ts=149.9)
    due_after = store.get_due_tasks(now_ts=150.0)

    assert due_before == []
    assert [item.task_id for item in due_after] == ["task-1"]


def test_mark_executing_is_atomic(tmp_path: Path) -> None:
    store = DelayStore(tmp_path / "delay_queue.jsonl")
    store.schedule(
        DelayedTask(
            task_id="task-1",
            rule_id="rule-1",
            trigger_at=100.0,
            payload={"k": "v"},
        )
    )

    first = store.mark_executing("task-1")
    second = store.mark_executing("task-1")

    assert first is True
    assert second is False


def test_cleanup_old_removes_terminal_but_keeps_scheduled(tmp_path: Path) -> None:
    store = DelayStore(tmp_path / "delay_queue.jsonl")
    store.schedule(
        DelayedTask(
            task_id="done-old",
            rule_id="rule-1",
            trigger_at=10.0,
            payload={},
            status="completed",
            created_at=10.0,
            executed_at=20.0,
        )
    )
    store.schedule(
        DelayedTask(
            task_id="failed-old",
            rule_id="rule-1",
            trigger_at=10.0,
            payload={},
            status="failed",
            created_at=10.0,
            executed_at=20.0,
            error_detail="boom",
        )
    )
    store.schedule(
        DelayedTask(
            task_id="scheduled-keep",
            rule_id="rule-1",
            trigger_at=10.0,
            payload={},
            status="scheduled",
            created_at=10.0,
        )
    )

    removed = store.cleanup_old(now_ts=200.0, retention_seconds=60.0)
    remaining = store.list_tasks()

    assert removed == 2
    assert [item.task_id for item in remaining] == ["scheduled-keep"]


def test_cancel_executing_task_returns_false(tmp_path: Path) -> None:
    store = DelayStore(tmp_path / "delay_queue.jsonl")
    store.schedule(
        DelayedTask(
            task_id="task-1",
            rule_id="rule-1",
            trigger_at=100.0,
            payload={"k": "v"},
        )
    )

    acquired = store.mark_executing("task-1")
    cancelled = store.cancel("task-1")

    assert acquired is True
    assert cancelled is False
