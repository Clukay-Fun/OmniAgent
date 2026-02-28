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

from src.adapters.channels.feishu.utils.reminder_target_adapter import map_target_conversation_id
from src.config import Settings
from src.utils.platform.feishu.feishu_api import send_message
from src.utils.observability.metrics import record_reminder_dispatch


@dataclass
class ReminderDispatchPayload:
    """
    Reminder 发送任务的数据结构

    属性:
        - source: 提醒来源
        - business_id: 业务ID
        - trigger_date: 触发日期
        - offset: 偏移量
        - receive_id: 接收者ID
        - receive_id_type: 接收者ID类型
        - content: 消息内容
        - msg_type: 消息类型，默认为 "text"
        - target_conversation_id: 目标会话ID
        - credential_source: 凭证来源，默认为 "default"
    """
    source: str
    business_id: str
    trigger_date: date | datetime | str
    offset: int
    receive_id: str
    receive_id_type: str
    content: dict[str, Any]
    msg_type: str = "text"
    target_conversation_id: str = ""
    credential_source: str = "default"


@dataclass
class ReminderDispatchResult:
    """
    Reminder 发送结果的数据结构

    属性:
        - status: 发送状态
        - dedupe_key: 幂等键
    """
    status: str
    dedupe_key: str


class ReminderDedupeStore(Protocol):
    """
    幂等键存储接口

    方法:
        - contains: 检查幂等键是否存在
        - add: 添加幂等键
    """
    def contains(self, dedupe_key: str) -> bool: ...

    def add(self, dedupe_key: str) -> None: ...


class InMemoryReminderDedupeStore:
    """
    内存中的幂等键存储实现

    方法:
        - contains: 检查幂等键是否存在
        - add: 添加幂等键
    """
    def __init__(self) -> None:
        self._keys: set[str] = set()

    def contains(self, dedupe_key: str) -> bool:
        return dedupe_key in self._keys

    def add(self, dedupe_key: str) -> None:
        self._keys.add(dedupe_key)


class ReminderDispatcher:
    """
    Reminder 分发器

    功能:
        - 构建幂等键
        - 分发 Reminder
        - 处理幂等键存储
    """
    def __init__(
        self,
        settings: Settings,
        dedupe_store: ReminderDedupeStore | None = None,
        sender: Any | None = None,
    ) -> None:
        """
        初始化 ReminderDispatcher

        参数:
            - settings: 配置设置
            - dedupe_store: 幂等键存储实例
            - sender: 发送消息的函数
        """
        self._settings = settings
        self._dedupe_store = dedupe_store or InMemoryReminderDedupeStore()
        self._sender = sender or send_message
        self._logger = logging.getLogger(__name__)

    def build_dedupe_key(self, payload: ReminderDispatchPayload) -> str:
        """
        构建幂等键

        参数:
            - payload: Reminder 发送任务的数据结构

        返回:
            - 幂等键字符串
        """
        source = str(payload.source or "unknown").strip() or "unknown"
        business_id = str(payload.business_id or "").strip()
        trigger_date = self._normalize_trigger_date(payload.trigger_date)
        offset = int(payload.offset)
        return f"{source}:{business_id}:{trigger_date}:{offset}"

    async def dispatch(self, payload: ReminderDispatchPayload) -> ReminderDispatchResult:
        """
        分发 Reminder

        参数:
            - payload: Reminder 发送任务的数据结构

        返回:
            - Reminder 发送结果的数据结构
        """
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
            resolved_receive_id, resolved_receive_id_type = self._resolve_receive_target(payload)
            await self._sender(
                settings=self._settings,
                receive_id=resolved_receive_id,
                msg_type=payload.msg_type,
                content=payload.content,
                receive_id_type=resolved_receive_id_type,
                credential_source=payload.credential_source,
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
        """
        触发日期标准化

        参数:
            - value: 触发日期，可以是 date, datetime 或 str 类型

        返回:
            - 标准化后的日期字符串
        """
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value or "").strip()

    def _resolve_receive_target(self, payload: ReminderDispatchPayload) -> tuple[str, str]:
        """
        解析接收目标

        参数:
            - payload: Reminder 发送任务的数据结构

        返回:
            - 解析后的接收者ID和接收者ID类型
        """
        mapped = map_target_conversation_id(payload.target_conversation_id)
        if mapped is not None:
            return mapped
        return str(payload.receive_id or "").strip(), str(payload.receive_id_type or "chat_id").strip() or "chat_id"
