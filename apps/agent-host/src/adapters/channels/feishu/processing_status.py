"""
描述: 该模块负责在飞书消息上根据处理状态添加或删除反应。
主要功能:
    - 根据处理状态事件在飞书消息上添加相应的反应。
    - 管理和更新消息上的反应，确保同一时间只有一个反应存在。
"""

from __future__ import annotations

import logging

from src.config import Settings
from src.core.processing_status import ProcessingStatus, ProcessingStatusEvent
from src.utils.feishu_api import delete_message_reaction, set_message_reaction

logger = logging.getLogger(__name__)

_REACTION_BY_STATUS: dict[ProcessingStatus, str] = {
    ProcessingStatus.THINKING: "OK",
    ProcessingStatus.SEARCHING: "OK",
}

class FeishuReactionStatusEmitter:
    """
    根据处理状态事件在飞书消息上添加或删除反应。

    功能:
        - 初始化时设置必要的参数。
        - 根据处理状态事件更新飞书消息上的反应。
    """

    def __init__(self, settings: Settings, message_id: str) -> None:
        self._settings = settings
        self._message_id = str(message_id or "").strip()
        self._disabled_statuses: set[ProcessingStatus] = set()
        # 上一阶段添加的 reaction_id，用于切换时先撤回
        self._last_reaction_id: str = ""
        self._last_reaction_type: str = ""

    async def __call__(self, event: ProcessingStatusEvent) -> None:
        """
        根据处理状态事件更新飞书消息上的反应。

        功能:
            - 检查消息ID是否存在。
            - 检查当前状态是否被禁用。
            - 获取当前状态对应的反应类型。
            - 如果当前状态的反应类型与上一次相同，则跳过。
            - 撤回上一次的反应。
            - 添加当前状态的反应。
        """
        if not self._message_id:
            return
        if event.status in self._disabled_statuses:
            return

        if event.status == ProcessingStatus.DONE:
            if not self._last_reaction_id:
                return
            try:
                await delete_message_reaction(
                    settings=self._settings,
                    message_id=self._message_id,
                    reaction_id=self._last_reaction_id,
                )
            except Exception as exc:
                logger.debug(
                    "撤回完成态前的 processing reaction 失败: %s",
                    exc,
                    extra={
                        "event_code": "feishu.processing_status.reaction_delete_failed",
                        "reaction_id": self._last_reaction_id,
                        "message_id": self._message_id,
                    },
                )
            self._last_reaction_id = ""
            self._last_reaction_type = ""
            return

        reaction_type = _REACTION_BY_STATUS.get(event.status)
        if not reaction_type:
            return

        if self._last_reaction_id and self._last_reaction_type == reaction_type:
            return

        # 1. 先撤回上一个阶段的 reaction，保持消息上只有一个表情
        if self._last_reaction_id:
            try:
                await delete_message_reaction(
                    settings=self._settings,
                    message_id=self._message_id,
                    reaction_id=self._last_reaction_id,
                )
            except Exception as exc:
                # 删除失败不阻断流程，只记录 debug
                logger.debug(
                    "撤回上一阶段 reaction 失败: %s",
                    exc,
                    extra={
                        "event_code": "feishu.processing_status.reaction_delete_failed",
                        "reaction_id": self._last_reaction_id,
                        "message_id": self._message_id,
                    },
                )
            self._last_reaction_id = ""
            self._last_reaction_type = ""

        # 2. 添加当前阶段的 reaction
        try:
            reaction_id = await set_message_reaction(
                settings=self._settings,
                message_id=self._message_id,
                reaction_type=reaction_type,
            )
            self._last_reaction_id = str(reaction_id or "")
            self._last_reaction_type = reaction_type
        except Exception as exc:
            error_text = str(exc).lower()
            if "reaction type is invalid" in error_text:
                self._disabled_statuses.add(event.status)
                logger.info(
                    "reaction 类型无效，已禁用该状态的 processing status reaction",
                    extra={
                        "event_code": "feishu.processing_status.reaction_disabled",
                        "status": event.status.value,
                        "message_id": self._message_id,
                    },
                )
                return
            logger.warning(
                "发送处理状态 reaction 失败: %s",
                exc,
                extra={
                    "event_code": "feishu.processing_status.reaction_failed",
                    "status": event.status.value,
                    "message_id": self._message_id,
                    "reaction_type": reaction_type,
                },
            )

# region 工厂函数
def create_reaction_status_emitter(settings: Settings, message_id: str) -> FeishuReactionStatusEmitter | None:
    """
    创建一个 FeishuReactionStatusEmitter 实例。

    功能:
        - 检查是否启用了反应功能。
        - 检查消息ID是否存在。
        - 返回一个 FeishuReactionStatusEmitter 实例或 None。
    """
    if not bool(getattr(getattr(settings, "reply", None), "reaction_enabled", False)):
        return None
    normalized_message_id = str(message_id or "").strip()
    if not normalized_message_id:
        return None
    return FeishuReactionStatusEmitter(settings=settings, message_id=normalized_message_id)
# endregion
