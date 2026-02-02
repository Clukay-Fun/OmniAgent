"""
描述: Reminder 定时推送调度器
主要功能:
    - 周期扫描到期提醒
    - 发送飞书消息
    - 失败重试与状态更新
依赖: APScheduler
"""

from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.db.postgres import PostgresClient
from src.utils.feishu_api import send_message
from src.utils.metrics import record_reminder_push
from src.config import Settings

logger = logging.getLogger(__name__)


class ReminderScheduler:
    def __init__(
        self,
        settings: Settings,
        db: PostgresClient,
        interval_seconds: int = 60,
        instance_id: str = "",
        lock_key: str = "reminder_scan",
        lock_timeout_seconds: int = 300,
        batch_limit: int = 50,
    ) -> None:
        self._settings = settings
        self._db = db
        self._interval = interval_seconds
        self._instance_id = instance_id
        self._lock_key = lock_key
        self._lock_timeout_seconds = lock_timeout_seconds
        self._batch_limit = batch_limit
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self._scheduler.add_job(self._scan_and_push, "interval", seconds=self._interval)
        self._scheduler.start()
        logger.info("Reminder scheduler started")

    async def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        await self._db.close()
        logger.info("Reminder scheduler stopped")

    async def _scan_and_push(self) -> None:
        async with self._db.advisory_lock(self._lock_key) as conn:
            if conn is None:
                logger.debug("Reminder scan skipped: lock held")
                return

            reminders = await self._db.list_due_reminders(
                conn=conn,
                instance_id=self._instance_id,
                lock_timeout_seconds=self._lock_timeout_seconds,
                limit=self._batch_limit,
            )
            if not reminders:
                return

            for reminder in reminders:
                await self._push_single(reminder)

    async def _push_single(self, reminder: dict[str, Any]) -> None:
        reminder_id = reminder.get("id")
        user_id = reminder.get("user_id")
        chat_id = reminder.get("chat_id")
        content = reminder.get("content", "")
        due_at = reminder.get("due_at")
        priority = reminder.get("priority", "medium")

        target = chat_id or user_id
        receive_id_type = "chat_id" if chat_id else "open_id"

        due_text = due_at.strftime("%Y-%m-%d %H:%M") if due_at else "未设置时间"
        message = {
            "text": f"⏰ 提醒到期\n\n📌 内容：{content}\n⏱ 时间：{due_text}\n⭐ 优先级：{priority}"
        }

        status = "success"
        try:
            await send_message(
                settings=self._settings,
                receive_id=target,
                msg_type="text",
                content=message,
                receive_id_type=receive_id_type,
            )
            await self._db.mark_reminder_sent(reminder_id)
        except Exception as exc:
            status = "failure"
            await self._db.mark_reminder_failed(reminder_id, str(exc))
            logger.error("Reminder push failed: %s", exc)
        finally:
            record_reminder_push(status)
