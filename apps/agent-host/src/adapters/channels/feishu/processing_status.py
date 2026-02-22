from __future__ import annotations

import logging

from src.config import Settings
from src.core.processing_status import ProcessingStatus, ProcessingStatusEvent
from src.utils.feishu_api import set_message_reaction

logger = logging.getLogger(__name__)


_REACTION_BY_STATUS: dict[ProcessingStatus, str] = {
    ProcessingStatus.THINKING: "⏳",
    ProcessingStatus.SEARCHING: "🔍",
    ProcessingStatus.DONE: "✅",
}


class FeishuReactionStatusEmitter:
    def __init__(self, settings: Settings, message_id: str) -> None:
        self._settings = settings
        self._message_id = str(message_id or "").strip()

    async def __call__(self, event: ProcessingStatusEvent) -> None:
        if not self._message_id:
            return
        reaction_type = _REACTION_BY_STATUS.get(event.status)
        if not reaction_type:
            return
        try:
            await set_message_reaction(
                settings=self._settings,
                message_id=self._message_id,
                reaction_type=reaction_type,
            )
        except Exception as exc:
            logger.warning(
                "发送处理状态 reaction 失败: %s",
                exc,
                extra={
                    "event_code": "feishu.processing_status.reaction_failed",
                    "status": event.status.value,
                    "message_id": self._message_id,
                },
            )


def create_reaction_status_emitter(settings: Settings, message_id: str) -> FeishuReactionStatusEmitter | None:
    if not bool(getattr(getattr(settings, "reply", None), "reaction_enabled", False)):
        return None
    normalized_message_id = str(message_id or "").strip()
    if not normalized_message_id:
        return None
    return FeishuReactionStatusEmitter(settings=settings, message_id=normalized_message_id)
