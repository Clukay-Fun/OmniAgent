from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
import sys
import types
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

if "apscheduler.schedulers.asyncio" not in sys.modules:
    apscheduler_module = types.ModuleType("apscheduler")
    schedulers_module = types.ModuleType("apscheduler.schedulers")
    asyncio_module = types.ModuleType("apscheduler.schedulers.asyncio")

    class _DummyAsyncIOScheduler:
        def add_job(self, *args: Any, **kwargs: Any) -> None:
            _ = args, kwargs

        def start(self) -> None:
            return None

        def shutdown(self, wait: bool = False) -> None:
            _ = wait

    asyncio_module.AsyncIOScheduler = _DummyAsyncIOScheduler
    sys.modules["apscheduler"] = apscheduler_module
    sys.modules["apscheduler.schedulers"] = schedulers_module
    sys.modules["apscheduler.schedulers.asyncio"] = asyncio_module

from src.jobs.hearing_reminder import HearingReminderScheduler
from src.jobs.reminder_dispatcher import (
    InMemoryReminderDedupeStore,
    ReminderDispatchPayload,
    ReminderDispatcher,
)
from src.jobs.reminder_scheduler import ReminderScheduler


def test_reminder_dispatcher_builds_shared_dedupe_key() -> None:
    dispatcher = ReminderDispatcher(settings=object(), sender=_noop_sender)
    payload = ReminderDispatchPayload(
        source="conversation",
        business_id="42",
        trigger_date=datetime(2026, 2, 22, 10, 30),
        offset=0,
        receive_id="ou_xxx",
        receive_id_type="open_id",
        content={"text": "hello"},
    )

    dedupe_key = dispatcher.build_dedupe_key(payload)

    assert dedupe_key == "conversation:42:2026-02-22:0"


def test_reminder_dispatcher_suppresses_duplicate_send() -> None:
    calls: list[dict[str, Any]] = []

    async def _sender(**kwargs: Any) -> None:
        calls.append(kwargs)

    dispatcher = ReminderDispatcher(
        settings=object(),
        dedupe_store=InMemoryReminderDedupeStore(),
        sender=_sender,
    )
    payload = ReminderDispatchPayload(
        source="hearing",
        business_id="rec_1",
        trigger_date=date(2026, 3, 1),
        offset=3,
        receive_id="oc_chat",
        receive_id_type="chat_id",
        content={"text": "x"},
    )

    first = asyncio.run(dispatcher.dispatch(payload))
    second = asyncio.run(dispatcher.dispatch(payload))

    assert first.status == "dispatched"
    assert second.status == "deduped"
    assert len(calls) == 1


def test_reminder_dispatcher_uses_org_b_credential_source() -> None:
    calls: list[dict[str, Any]] = []

    async def _sender(**kwargs: Any) -> None:
        calls.append(kwargs)

    dispatcher = ReminderDispatcher(settings=object(), sender=_sender)
    payload = ReminderDispatchPayload(
        source="conversation",
        business_id="orgb-1",
        trigger_date="2026-02-22",
        offset=0,
        receive_id="ignored-open-id",
        receive_id_type="open_id",
        target_conversation_id="oc_chat_001",
        credential_source="org_b",
        content={"text": "hello"},
    )

    result = asyncio.run(dispatcher.dispatch(payload))

    assert result.status == "dispatched"
    assert len(calls) == 1
    assert calls[0]["credential_source"] == "org_b"
    assert calls[0]["receive_id"] == "oc_chat_001"
    assert calls[0]["receive_id_type"] == "chat_id"


def test_reminder_dispatcher_dedupe_store_failure_is_non_blocking() -> None:
    calls: list[dict[str, Any]] = []

    class _BrokenStore:
        def contains(self, dedupe_key: str) -> bool:
            _ = dedupe_key
            raise RuntimeError("contains failed")

        def add(self, dedupe_key: str) -> None:
            _ = dedupe_key
            raise RuntimeError("add failed")

    async def _sender(**kwargs: Any) -> None:
        calls.append(kwargs)

    dispatcher = ReminderDispatcher(settings=object(), dedupe_store=_BrokenStore(), sender=_sender)
    result = asyncio.run(
        dispatcher.dispatch(
            ReminderDispatchPayload(
                source="conversation",
                business_id="99",
                trigger_date="2026-02-22",
                offset=0,
                receive_id="ou_xxx",
                receive_id_type="open_id",
                content={"text": "hello"},
            )
        )
    )

    assert result.status == "dispatched"
    assert len(calls) == 1


def test_conversation_scheduler_dispatches_via_dispatcher_and_marks_sent() -> None:
    db = _FakeReminderDB()
    dispatcher = _FakeDispatcher(results=["dispatched"])
    scheduler = ReminderScheduler(settings=object(), db=db, dispatcher=dispatcher)

    asyncio.run(
        scheduler._push_single(
            {
                "id": 123,
                "user_id": "ou_123",
                "chat_id": None,
                "content": "提交周报",
                "due_at": datetime(2026, 2, 22, 18, 0),
                "priority": "high",
            }
        )
    )

    assert len(dispatcher.payloads) == 1
    assert dispatcher.payloads[0].source == "conversation"
    assert dispatcher.payloads[0].business_id == "123"
    assert dispatcher.payloads[0].credential_source == "org_b"
    assert db.sent_ids == [123]


def test_hearing_scheduler_dispatches_via_dispatcher() -> None:
    dispatcher = _FakeDispatcher(results=["dispatched"])
    scheduler = HearingReminderScheduler(
        settings=object(),
        mcp_client=object(),
        reminder_chat_id="oc_chat",
        dispatcher=dispatcher,
    )

    asyncio.run(
        scheduler._send_reminder(
            {
                "record_id": "rec_100",
                "record_url": "https://example.com/rec_100",
                "fields_text": {
                    "案号": "(2026)沪01民初100号",
                    "案由": "合同纠纷",
                    "审理法院": "上海某法院",
                    "主办律师": "张三",
                },
            },
            offset=1,
            hearing_date=date(2026, 2, 23),
        )
    )

    assert len(dispatcher.payloads) == 1
    assert dispatcher.payloads[0].source == "hearing"
    assert dispatcher.payloads[0].business_id == "rec_100"
    assert dispatcher.payloads[0].offset == 1
    assert dispatcher.payloads[0].credential_source == "org_b"


def test_conversation_scheduler_continues_after_dispatch_failure() -> None:
    db = _FakeReminderDB(
        due_reminders=[
            {"id": 1, "user_id": "ou_1", "content": "A", "due_at": datetime(2026, 2, 22, 9, 0), "priority": "medium"},
            {"id": 2, "user_id": "ou_2", "content": "B", "due_at": datetime(2026, 2, 22, 9, 5), "priority": "medium"},
        ]
    )
    dispatcher = _FakeDispatcher(results=[RuntimeError("boom"), "dispatched"])
    scheduler = ReminderScheduler(settings=object(), db=db, dispatcher=dispatcher)

    asyncio.run(scheduler._scan_and_push())

    assert db.pending_retry_ids == [1]
    assert db.sent_ids == [2]
    assert len(dispatcher.payloads) == 2


def test_conversation_scheduler_marks_pending_retry_on_send_failure() -> None:
    db = _FakeReminderDB()
    dispatcher = _FakeDispatcher(results=[RuntimeError("credential/send failed")])
    scheduler = ReminderScheduler(settings=object(), db=db, dispatcher=dispatcher)

    asyncio.run(
        scheduler._push_single(
            {
                "id": 88,
                "user_id": "ou_88",
                "chat_id": "oc_retry",
                "content": "待跟进",
                "due_at": datetime(2026, 2, 22, 20, 0),
                "priority": "high",
            }
        )
    )

    assert db.pending_retry_ids == [88]
    assert db.sent_ids == []


class _FakeReminderDB:
    def __init__(self, due_reminders: list[dict[str, Any]] | None = None) -> None:
        self._due_reminders = due_reminders or []
        self.sent_ids: list[int] = []
        self.failed_ids: list[int] = []
        self.pending_retry_ids: list[int] = []

    @asynccontextmanager
    async def advisory_lock(self, key: str):
        _ = key
        yield object()

    async def list_due_reminders(
        self,
        conn: Any,
        instance_id: str,
        lock_timeout_seconds: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        _ = conn, instance_id, lock_timeout_seconds, limit
        return list(self._due_reminders)

    async def mark_reminder_sent(self, reminder_id: int) -> None:
        self.sent_ids.append(reminder_id)

    async def mark_reminder_failed(self, reminder_id: int, error: str) -> None:
        _ = error
        self.failed_ids.append(reminder_id)

    async def mark_reminder_pending_retry(self, reminder_id: int, error: str) -> None:
        _ = error
        self.pending_retry_ids.append(reminder_id)

    async def close(self) -> None:
        return None


class _FakeDispatcher:
    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self.payloads: list[ReminderDispatchPayload] = []

    async def dispatch(self, payload: ReminderDispatchPayload):
        self.payloads.append(payload)
        current = self._results.pop(0)
        if isinstance(current, Exception):
            raise current
        return type("_Result", (), {"status": current})()


async def _noop_sender(**kwargs: Any) -> None:
    _ = kwargs
