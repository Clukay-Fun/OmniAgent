"""
描述: Reminder 统一分发器
主要功能:
    - 统一 reminder 发送入口（对话提醒 / 开庭提醒）
    - 幂等键构建与去重
    - 结构化日志与指标埋点
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import logging
from typing import Any, Protocol

from src.config import Settings
from src.utils.feishu_api import send_message
from src.utils.metrics import record_reminder_dispatch


@dataclass
class ReminderDispatchPayload:
    source: str
    business_id: str
    trigger_date: date | datetime | str
    offset: int
    receive_id: str
    receive_id_type: str
    content: dict[str, Any]
    msg_type: str = "text"


@dataclass
class ReminderDispatchResult:
    status: str
    dedupe_key: str


class ReminderDedupeStore(Protocol):
    def contains(self, dedupe_key: str) -> bool: ...

    def add(self, dedupe_key: str) -> None: ...


class InMemoryReminderDedupeStore:
    def __init__(self) -> None:
        self._keys: set[str] = set()

    def contains(self, dedupe_key: str) -> bool:
        return dedupe_key in self._keys

    def add(self, dedupe_key: str) -> None:
        self._keys.add(dedupe_key)


class ReminderDispatcher:
    def __init__(
        self,
        settings: Settings,
        dedupe_store: ReminderDedupeStore | None = None,
        sender: Any | None = None,
    ) -> None:
        self._settings = settings
        self._dedupe_store = dedupe_store or InMemoryReminderDedupeStore()
        self._sender = sender or send_message
        self._logger = logging.getLogger(__name__)

    def build_dedupe_key(self, payload: ReminderDispatchPayload) -> str:
        source = str(payload.source or "unknown").strip() or "unknown"
        business_id = str(payload.business_id or "").strip()
        trigger_date = self._normalize_trigger_date(payload.trigger_date)
        offset = int(payload.offset)
        return f"{source}:{business_id}:{trigger_date}:{offset}"

    async def dispatch(self, payload: ReminderDispatchPayload) -> ReminderDispatchResult:
        dedupe_key = self.build_dedupe_key(payload)

        try:
            if self._dedupe_store.contains(dedupe_key):
                record_reminder_dispatch(payload.source, "deduped")
                self._logger.info(
                    "reminder dispatch deduped",
                    extra={
                        "event_code": "reminder.dispatcher.deduped",
                        "source": payload.source,
                        "business_id": payload.business_id,
                        "dedupe_key": dedupe_key,
                    },
                )
                return ReminderDispatchResult(status="deduped", dedupe_key=dedupe_key)
        except Exception:
            record_reminder_dispatch(payload.source, "dedupe_check_failed")
            self._logger.warning(
                "reminder dedupe check failed, continue send",
                extra={
                    "event_code": "reminder.dispatcher.dedupe_check_failed",
                    "source": payload.source,
                    "business_id": payload.business_id,
                },
                exc_info=True,
            )

        try:
            await self._sender(
                settings=self._settings,
                receive_id=payload.receive_id,
                msg_type=payload.msg_type,
                content=payload.content,
                receive_id_type=payload.receive_id_type,
            )
        except Exception:
            record_reminder_dispatch(payload.source, "failed")
            self._logger.exception(
                "reminder dispatch failed",
                extra={
                    "event_code": "reminder.dispatcher.failed",
                    "source": payload.source,
                    "business_id": payload.business_id,
                    "dedupe_key": dedupe_key,
                },
            )
            raise

        try:
            self._dedupe_store.add(dedupe_key)
        except Exception:
            record_reminder_dispatch(payload.source, "dedupe_store_failed")
            self._logger.warning(
                "reminder dedupe store failed after send",
                extra={
                    "event_code": "reminder.dispatcher.dedupe_store_failed",
                    "source": payload.source,
                    "business_id": payload.business_id,
                    "dedupe_key": dedupe_key,
                },
                exc_info=True,
            )
            return ReminderDispatchResult(status="dispatched", dedupe_key=dedupe_key)

        record_reminder_dispatch(payload.source, "dispatched")
        self._logger.info(
            "reminder dispatched",
            extra={
                "event_code": "reminder.dispatcher.dispatched",
                "source": payload.source,
                "business_id": payload.business_id,
                "dedupe_key": dedupe_key,
            },
        )
        return ReminderDispatchResult(status="dispatched", dedupe_key=dedupe_key)

    def _normalize_trigger_date(self, value: date | datetime | str) -> str:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value or "").strip()
