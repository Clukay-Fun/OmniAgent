"""
描述: 自动化消费骨架（队列 + run log）
主要功能:
    - 接收 event_router 入队的标准 payload
    - 受 startup_mode 保护地消费并落盘 run_logs.jsonl
    - 输出结构化日志与消费指标
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
from typing import Any

from src.utils.metrics import record_automation_consumed
from src.utils.workspace import get_workspace_root


def _read_bool_env(key: str, default: bool) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_run_log_path() -> Path:
    configured = str(os.getenv("AUTOMATION_RUN_LOG_FILE", "")).strip()
    if configured:
        path = Path(configured)
        if path.is_absolute():
            return path
        return Path.cwd() / path
    return get_workspace_root() / "automation_data" / "run_logs.jsonl"


@dataclass
class AutomationStartupGate:
    startup_mode: str = "auto"
    baseline_ready: bool = False

    def should_block(self) -> bool:
        normalized_mode = self.startup_mode.strip().lower()
        if normalized_mode != "auto":
            return False
        return not self.baseline_ready


class InMemoryAutomationQueue:
    def __init__(self) -> None:
        self._items: deque[dict[str, Any]] = deque()

    def enqueue(self, payload: dict[str, Any]) -> None:
        self._items.append(dict(payload))

    def pop_left(self) -> dict[str, Any] | None:
        if not self._items:
            return None
        return self._items.popleft()

    def push_front(self, payload: dict[str, Any]) -> None:
        self._items.appendleft(dict(payload))

    def size(self) -> int:
        return len(self._items)


class AutomationConsumer:
    def __init__(self, run_log_path: Path, startup_gate: AutomationStartupGate) -> None:
        self._run_log_path = run_log_path
        self._startup_gate = startup_gate
        self._logger = logging.getLogger(__name__)

    def consume_available(self, queue: InMemoryAutomationQueue, max_items: int = 100) -> int:
        processed = 0
        while processed < max_items:
            payload = queue.pop_left()
            if payload is None:
                break

            status = self._consume_single(payload)
            if status == "blocked_startup_protection":
                queue.push_front(payload)
                break
            processed += 1
        return processed

    def _consume_single(self, payload: dict[str, Any]) -> str:
        event_type = str(payload.get("event_type") or "unknown")
        event_id = str(payload.get("event_id") or "")
        if self._startup_gate.should_block():
            record_automation_consumed(event_type, "blocked_startup_protection")
            self._logger.info(
                "automation consume blocked by startup protection",
                extra={
                    "event_code": "automation.consumer.blocked_startup_protection",
                    "event_id": event_id,
                    "event_type": event_type,
                    "startup_mode": self._startup_gate.startup_mode,
                    "baseline_ready": self._startup_gate.baseline_ready,
                },
            )
            return "blocked_startup_protection"

        record = {
            "event_id": event_id,
            "event_type": event_type,
            "app_token": str(payload.get("app_token") or ""),
            "table_id": str(payload.get("table_id") or ""),
            "record_id": str(payload.get("record_id") or ""),
            "changed_fields": payload.get("changed_fields") or [],
            "occurred_at": str(payload.get("occurred_at") or ""),
            "status": "consumed",
        }

        self._logger.info(
            "automation consume started",
            extra={
                "event_code": "automation.consumer.consume_started",
                "event_id": event_id,
                "event_type": event_type,
            },
        )

        try:
            self._run_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._run_log_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
            record_automation_consumed(event_type, "consumed")
            self._logger.info(
                "automation consume logged",
                extra={
                    "event_code": "automation.consumer.logged",
                    "event_id": event_id,
                    "event_type": event_type,
                    "run_log_path": str(self._run_log_path),
                },
            )
            return "consumed"
        except Exception:
            record_automation_consumed(event_type, "failed")
            self._logger.exception(
                "automation consume failed",
                extra={
                    "event_code": "automation.consumer.failed",
                    "event_id": event_id,
                    "event_type": event_type,
                    "run_log_path": str(self._run_log_path),
                },
            )
            return "failed"


class QueueAutomationEnqueuer:
    def __init__(self, queue: InMemoryAutomationQueue, consumer: AutomationConsumer) -> None:
        self._queue = queue
        self._consumer = consumer
        self._logger = logging.getLogger(__name__)

    def enqueue_record_changed(self, event_payload: dict[str, Any]) -> bool:
        self._queue.enqueue(event_payload)
        self._logger.info(
            "automation payload enqueued",
            extra={
                "event_code": "automation.enqueuer.enqueued",
                "event_id": str(event_payload.get("event_id") or ""),
                "event_type": str(event_payload.get("event_type") or ""),
                "queue_size": self._queue.size(),
            },
        )
        self._consumer.consume_available(self._queue)
        return True


def create_default_automation_enqueuer() -> QueueAutomationEnqueuer:
    startup_mode = str(os.getenv("AUTOMATION_STARTUP_MODE", "auto") or "auto")
    baseline_ready = _read_bool_env("AUTOMATION_BASELINE_READY", False)
    startup_gate = AutomationStartupGate(startup_mode=startup_mode, baseline_ready=baseline_ready)
    queue = InMemoryAutomationQueue()
    consumer = AutomationConsumer(run_log_path=_resolve_run_log_path(), startup_gate=startup_gate)
    return QueueAutomationEnqueuer(queue=queue, consumer=consumer)
