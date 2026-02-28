from __future__ import annotations

import multiprocessing
from pathlib import Path
import sys
from typing import Any

import pytest


MCP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_ROOT))

from src.automation.delay_store import DelayedTask, DelayStore


def _mark_executing_once(delay_queue_file: str, gate: Any, out_q: Any) -> None:
    store = DelayStore(delay_queue_file)
    gate.wait(timeout=5)
    out_q.put(store.mark_executing("task-1"))


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


def test_mark_executing_is_atomic_across_processes(tmp_path: Path) -> None:
    delay_queue_file = tmp_path / "delay_queue.jsonl"
    store = DelayStore(delay_queue_file)
    store.schedule(
        DelayedTask(
            task_id="task-1",
            rule_id="rule-1",
            trigger_at=100.0,
            payload={"k": "v"},
        )
    )

    ctx = multiprocessing.get_context("spawn")
    gate = ctx.Event()
    out_q = ctx.Queue()
    workers = [ctx.Process(target=_mark_executing_once, args=(str(delay_queue_file), gate, out_q)) for _ in range(4)]

    for worker in workers:
        worker.start()
    gate.set()

    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0

    results = [out_q.get(timeout=2) for _ in workers]
    assert results.count(True) == 1
    assert results.count(False) == len(workers) - 1


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


@pytest.mark.parametrize(
    "status, expected",
    [
        ("scheduled", True),
        ("executing", False),
        ("completed", False),
        ("failed", False),
        ("cancelled", False),
    ],
)
def test_cancel_state_machine_matrix(tmp_path: Path, status: str, expected: bool) -> None:
    store = DelayStore(tmp_path / "delay_queue.jsonl")
    store.schedule(
        DelayedTask(
            task_id="task-1",
            rule_id="rule-1",
            trigger_at=100.0,
            payload={"k": "v"},
            status=status,
        )
    )

    cancelled = store.cancel("task-1")

    assert cancelled is expected
