"""
描述: 提供核心错误类型的定义和用户消息解析功能。
主要功能:
    - 定义多种核心错误类型，每个错误类型都有一个对应的代码。
    - 加载并解析 error_messages.yaml 文件，根据错误代码获取用户友好的消息。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class CoreError(Exception):
    """核心错误基类，所有类型化的核心错误都继承自该类。

    功能:
        - 初始化时可选地设置错误代码。
        - 如果未提供错误代码，则使用默认的 "unknown_error"。
    """
    code: str = "unknown_error"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        if code:
            self.code = code
        super().__init__(message or self.code)


class PendingActionExpiredError(CoreError):
    """待处理操作已过期错误。

    功能:
        - 表示某个待处理操作已经过期。
    """
    code = "pending_action_expired"


class PendingActionNotFoundError(CoreError):
    """待处理操作未找到错误。

    功能:
        - 表示尝试访问的待处理操作不存在。
    """
    code = "pending_action_not_found"


class LocatorTripletMissingError(CoreError):
    """定位三元组缺失错误。

    功能:
        - 表示在需要定位三元组的情况下，三元组缺失。
    """
    code = "locator_triplet_missing"


class CallbackDuplicatedError(CoreError):
    """回调重复错误。

    功能:
        - 表示尝试注册的回调已经存在。
    """
    code = "callback_duplicated"


class WritePermissionDeniedError(CoreError):
    """写权限被拒绝错误。

    功能:
        - 表示尝试执行写操作时，权限被拒绝。
    """
    code = "write_permission_denied"


# region error catalog loader
_ERROR_CATALOG: dict[str, str] | None = None


def _load_error_catalog() -> dict[str, str]:
    """加载错误消息目录。

    功能:
        - 从 error_messages.yaml 文件加载错误消息目录。
        - 如果文件不存在或加载失败，则返回空目录。
    """
    global _ERROR_CATALOG
    if _ERROR_CATALOG is not None:
        return _ERROR_CATALOG
    path = Path(__file__).resolve().parents[2] / "config" / "error_messages.yaml"
    if not path.exists():
        _ERROR_CATALOG = {}
        return _ERROR_CATALOG
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        raw = {}
    catalog: dict[str, str] = {}
    if isinstance(raw, dict):
        for code, entry in raw.items():
            if isinstance(entry, dict):
                catalog[str(code)] = str(entry.get("user_message") or entry.get("message") or "")
            elif isinstance(entry, str):
                catalog[str(code)] = entry
    _ERROR_CATALOG = catalog
    return _ERROR_CATALOG


def get_user_message_by_code(code: str, *, fallback: str = "", **kwargs: Any) -> str:
    """根据错误代码解析用户友好的消息。

    功能:
        - 从错误消息目录中获取对应代码的用户消息。
        - 如果未找到对应消息，则使用默认的 "unknown_error" 消息。
        - 如果仍然未找到消息，则使用提供的 fallback 消息或错误代码本身。
        - 支持使用模板字符串格式化消息。
    """
    catalog = _load_error_catalog()
    normalized = str(code or "").strip()
    template = ""
    if normalized:
        value = str(catalog.get(normalized) or "").strip()
        if value:
            template = value

    if not template:
        unknown = str(catalog.get("unknown_error") or "").strip()
        if unknown:
            template = unknown

    if not template:
        if fallback:
            template = fallback
        else:
            template = normalized or "unknown error"

    if kwargs:
        try:
            return template.format(**kwargs)
        except KeyError:
            return template
    return template


def get_user_message(error: CoreError) -> str:
    """解析类型化错误的用户友好的消息。

    功能:
        - 使用错误对象的代码从错误消息目录中获取用户消息。
        - 如果未找到对应消息，则使用错误对象的默认消息。
    """
    return get_user_message_by_code(error.code, fallback=str(error))
# endregion
