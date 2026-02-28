"""Conversation reminder scheduler."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.jobs.dispatchers.reminder_dispatcher import ReminderDispatchPayload, ReminderDispatcher

logger = logging.getLogger(__name__)


class ReminderScheduler:
    """Scan due reminders and dispatch through ReminderDispatcher."""

    def __init__(
        self,
        settings: Any,
        db: Any,
        dispatcher: ReminderDispatcher,
        interval_minutes: int = 1,
        batch_size: int = 100,
    ) -> None:
        self._settings = settings
        self._db = db
        self._dispatcher = dispatcher
        self._interval_minutes = max(1, int(interval_minutes))
        self._batch_size = max(1, int(batch_size))
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self._scheduler.add_job(
            self._scan_and_push,
            "interval",
            minutes=self._interval_minutes,
            coalesce=True,
            max_instances=1,
        )
        self._scheduler.start()

    async def stop(self) -> None:
        self._scheduler.shutdown(wait=False)

    async def _scan_and_push(self) -> None:
        try:
            async with self._db.advisory_lock("conversation_reminder_scan") as conn:
                reminders = await self._db.list_due_reminders(
                    conn=conn,
                    instance_id="agent-host",
                    lock_timeout_seconds=30,
                    limit=self._batch_size,
                )
            for reminder in reminders:
                await self._push_single(reminder)
        except Exception:
            logger.exception("conversation reminder scan failed")

    async def _push_single(self, reminder: dict[str, Any]) -> None:
        reminder_id = int(reminder.get("id") or 0)
        user_id = str(reminder.get("user_id") or "").strip()
        chat_id = str(reminder.get("chat_id") or "").strip()
        due_at = reminder.get("due_at")
        content = str(reminder.get("content") or "").strip()
        priority = str(reminder.get("priority") or "medium").strip() or "medium"

        receive_id = chat_id or user_id
        receive_id_type = "chat_id" if chat_id else "open_id"
        target_conversation_id = chat_id or ""

        text = self._build_message(content=content, due_at=due_at, priority=priority)
        payload = ReminderDispatchPayload(
            source="conversation",
            business_id=str(reminder_id),
            trigger_date=due_at if due_at is not None else datetime.now().date(),
            offset=0,
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            content={"text": text},
            msg_type="text",
            target_conversation_id=target_conversation_id,
            credential_source="org_b",
        )

        try:
            result = await self._dispatcher.dispatch(payload)
        except Exception as exc:
            await self._db.mark_reminder_pending_retry(reminder_id, str(exc))
            return

        if getattr(result, "status", "") in {"dispatched", "deduped"}:
            await self._db.mark_reminder_sent(reminder_id)

    def _build_message(self, *, content: str, due_at: Any, priority: str) -> str:
        lines = ["⏰ 日程提醒", ""]
        if content:
            lines.append(f"事项：{content}")
        if isinstance(due_at, datetime):
            lines.append(f"时间：{due_at.strftime('%Y-%m-%d %H:%M')}")
        if priority:
            lines.append(f"优先级：{priority}")
        return "\n".join(lines)
