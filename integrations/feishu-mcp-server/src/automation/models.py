"""
描述: 自动化服务层共享模型与工具函数。
主要功能:
    - 统一错误类型与事件常量
    - 提供服务层通用数据归一化工具
"""

from __future__ import annotations

from typing import Any

from src.automation.delay_store import CANCELLED, COMPLETED, EXECUTING, FAILED, SCHEDULED


EVENT_TYPE_RECORD_CHANGED = "drive.file.bitable_record_changed_v1"
EVENT_TYPE_FIELD_CHANGED = "drive.file.bitable_field_changed_v1"
SUPPORTED_EVENT_TYPES = {EVENT_TYPE_RECORD_CHANGED, EVENT_TYPE_FIELD_CHANGED}
VALID_DELAY_STATUSES = {SCHEDULED, EXECUTING, COMPLETED, FAILED, CANCELLED}


class AutomationValidationError(ValueError):
    """自动化请求校验错误。"""


def _normalize_record_id(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_int_timestamp(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _is_watch_mode_full(plan: dict[str, Any]) -> bool:
    return str(plan.get("mode") or "full") == "full"


def _watch_fields(plan: dict[str, Any]) -> list[str]:
    fields = plan.get("fields")
    if not isinstance(fields, list):
        return []
    result: list[str] = []
    for item in fields:
        name = str(item or "").strip()
        if name:
            result.append(name)
    return result


__all__ = [
    "AutomationValidationError",
    "EVENT_TYPE_FIELD_CHANGED",
    "EVENT_TYPE_RECORD_CHANGED",
    "SUPPORTED_EVENT_TYPES",
    "VALID_DELAY_STATUSES",
    "_is_watch_mode_full",
    "_normalize_record_id",
    "_to_int_timestamp",
    "_watch_fields",
]
