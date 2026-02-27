"""
描述: 提供字段值格式化的功能，根据字段元数据将不同类型的值格式化为统一的文本表示。
主要功能:
    - 根据字段类型格式化字段值
    - 处理文本、数字、日期、选择、人员、布尔值和附件等多种字段类型
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


_LOCAL_TZ = timezone(timedelta(hours=8))
_FIELD_TYPE_TEXT = {1}
_FIELD_TYPE_NUMBER = {2}
_FIELD_TYPE_SINGLE_SELECT = {3}
_FIELD_TYPE_MULTI_SELECT = {4}
_FIELD_TYPE_DATE = {5, 6, 23, 1003}
_FIELD_TYPE_BOOL = {7}
_FIELD_TYPE_PERSON = {11, 1001, 1002}
_FIELD_TYPE_ATTACHMENT = {17}


@dataclass(frozen=True)
class FieldFormatResult:
    """
    字段格式化结果的数据类

    属性:
        - text: 格式化后的文本
        - field_type: 字段类型
        - status: 格式化状态
    """
    text: str
    field_type: str
    status: str


# region 辅助函数
def _safe_text(value: Any) -> str:
    """
    安全地将任意值转换为字符串

    功能:
        - 如果值为None，返回空字符串
        - 否则，返回值的字符串表示
    """
    if value is None:
        return ""
    return str(value)


def _to_decimal(value: Any) -> Decimal | None:
    """
    将任意值转换为Decimal类型

    功能:
        - 处理布尔值、整数、浮点数和Decimal类型
        - 处理字符串形式的数字，支持逗号分隔
        - 如果转换失败，返回None
    """
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    raw = _safe_text(value).replace(",", "").strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _format_number(value: Any) -> tuple[str, str]:
    """
    格式化数字值

    功能:
        - 将值转换为Decimal类型
        - 如果转换失败，返回原始值和"malformed"状态
        - 格式化为整数或带两位小数的字符串
    """
    number = _to_decimal(value)
    if number is None:
        return _safe_text(value), "malformed"
    normalized = number.normalize()
    if normalized == normalized.to_integral():
        return format(int(normalized), ","), "success"
    text = f"{number:,.2f}".rstrip("0").rstrip(".")
    return text, "success"


def _format_currency(value: Any) -> tuple[str, str]:
    """
    格式化货币值

    功能:
        - 将值转换为Decimal类型
        - 如果转换失败，返回原始值和"malformed"状态
        - 格式化为带货币符号的字符串
    """
    number = _to_decimal(value)
    if number is None:
        return _safe_text(value), "malformed"
    return f"\u00a5{number:,.2f}", "success"


def _parse_datetime(value: Any) -> datetime | None:
    """
    解析日期时间值

    功能:
        - 处理datetime对象、时间戳和ISO格式的字符串
        - 支持毫秒级和秒级时间戳
        - 如果解析失败，返回None
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1_000_000_000_000:
            ts = ts / 1000
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    text = _safe_text(value).strip()
    if not text:
        return None
    if text.isdigit():
        ts = float(text)
        if ts > 1_000_000_000_000:
            ts = ts / 1000
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_datetime(value: Any) -> tuple[str, str]:
    """
    格式化日期时间值

    功能:
        - 解析日期时间值
        - 如果解析失败，返回原始值和"malformed"状态
        - 格式化为本地时间的字符串
    """
    dt = _parse_datetime(value)
    if dt is None:
        return _safe_text(value), "malformed"
    local = dt.astimezone(_LOCAL_TZ)
    return local.strftime("%Y年%m月%d日 %H:%M"), "success"


def _format_select(value: Any) -> str:
    """
    格式化选择值

    功能:
        - 处理字典和列表类型的选择值
        - 提取标签、名称、文本或值字段
        - 如果值为空，返回空字符串
    """
    if isinstance(value, dict):
        for key in ("label", "name", "text", "value"):
            candidate = value.get(key)
            if candidate:
                return str(candidate)
        return _safe_text(value)
    if isinstance(value, list):
        labels = [_format_select(item) for item in value]
        labels = [item for item in labels if item]
        return "、".join(labels)
    return _safe_text(value)


def _format_multi_select(value: Any) -> tuple[str, str]:
    """
    格式化多选值

    功能:
        - 使用_format_select函数处理多选值
        - 如果格式化成功，返回格式化后的文本和"success"状态
        - 否则，返回原始值和"malformed"状态
    """
    text = _format_select(value)
    if text:
        return text, "success"
    return _safe_text(value), "malformed"


def _format_person(value: Any) -> tuple[str, str]:
    """
    格式化人员值

    功能:
        - 处理字典和列表类型的人员值
        - 提取用户名称或ID
        - 如果值为空，返回空字符串和"malformed"状态
        - 如果值为有效的用户名称，返回格式化后的字符串和"success"状态
    """
    if isinstance(value, dict):
        nested_users = value.get("users") or value.get("value")
        if isinstance(nested_users, list):
            return _format_person(nested_users)
    if isinstance(value, list):
        rendered = [_format_person(item)[0] for item in value]
        rendered = [item for item in rendered if item]
        return "、".join(rendered), "success"
    if isinstance(value, dict):
        name = value.get("name") or value.get("display_name") or value.get("en_name")
        if name:
            return f"@{name}", "success"
        fallback = value.get("user_id") or value.get("open_id") or value.get("id")
        return _safe_text(fallback), "fallback"
    text = _safe_text(value).strip()
    if not text:
        return "", "malformed"
    if text.startswith("@"):
        return text, "success"
    return f"@{text}", "success"


def _format_bool(value: Any) -> tuple[str, str]:
    """
    格式化布尔值

    功能:
        - 处理布尔类型和字符串形式的布尔值
        - 支持多种表示方式（如"1", "true", "yes", "y", "on"）
        - 如果值为空或无效，返回原始值和"malformed"状态
    """
    if isinstance(value, bool):
        return ("OK 是" if value else "OK 否"), "success"
    normalized = _safe_text(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return "OK 是", "success"
    if normalized in {"0", "false", "no", "n", "off", ""}:
        return "OK 否", "success"
    return _safe_text(value), "malformed"


def _format_attachment(value: Any) -> tuple[str, str]:
    """
    格式化附件值

    功能:
        - 处理列表和字典类型的附件值
        - 提取文件名
        - 如果值为空，返回空字符串和"malformed"状态
        - 如果值为有效的文件名，返回格式化后的字符串和"success"状态
    """
    def _extract_name(item: Any) -> str:
        if isinstance(item, dict):
            return _safe_text(item.get("name") or item.get("file_name") or item.get("filename"))
        return _safe_text(item)

    if isinstance(value, list):
        names = [_extract_name(item) for item in value]
        names = [name for name in names if name]
        if not names:
            return "", "malformed"
        return "、".join([f"OK {name}" for name in names]), "success"

    if isinstance(value, dict):
        nested_files = value.get("files") or value.get("value")
        if isinstance(nested_files, list):
            return _format_attachment(nested_files)

    name = _extract_name(value)
    if not name:
        return _safe_text(value), "malformed"
    return f"OK {name}", "success"
# endregion


# region 字段类型解析
def _is_currency(field_meta: dict[str, Any] | None) -> bool:
    """
    判断字段是否为货币类型

    功能:
        - 检查字段元数据中的类型名称、标签、标题和名称字段
        - 如果包含"currency", "货币", "金额", "price", "fee"等关键词，返回True
    """
    if not isinstance(field_meta, dict):
        return False
    candidates = [
        _safe_text(field_meta.get("type_name")).lower(),
        _safe_text(field_meta.get("label")).lower(),
        _safe_text(field_meta.get("title")).lower(),
        _safe_text(field_meta.get("name")).lower(),
    ]
    return any(token in text for text in candidates for token in ("currency", "货币", "金额", "price", "fee"))


def _resolve_kind(field_meta: dict[str, Any] | None) -> str:
    """
    解析字段类型

    功能:
        - 根据字段元数据中的类型字段确定字段类型
        - 支持文本、数字、日期、选择、人员、布尔值和附件等多种类型
        - 如果无法确定类型，返回"unknown"
    """
    if not isinstance(field_meta, dict):
        return "unknown"
    raw_type = _safe_text(field_meta.get("type")).strip()
    try:
        field_type = int(raw_type) if raw_type else -1
    except (TypeError, ValueError):
        field_type = -1

    if field_type in _FIELD_TYPE_TEXT:
        return "text"
    if field_type in _FIELD_TYPE_NUMBER:
        if _is_currency(field_meta):
            return "currency"
        return "number"
    if field_type in _FIELD_TYPE_DATE:
        return "datetime"
    if field_type in _FIELD_TYPE_SINGLE_SELECT:
        return "single_select"
    if field_type in _FIELD_TYPE_MULTI_SELECT:
        return "multi_select"
    if field_type in _FIELD_TYPE_PERSON:
        return "person"
    if field_type in _FIELD_TYPE_BOOL:
        return "bool"
    if field_type in _FIELD_TYPE_ATTACHMENT:
        return "attachment"
    return "unknown"
# endregion


def format_field_value(value: Any, field_meta: dict[str, Any] | None = None) -> FieldFormatResult:
    """
    格式化字段值

    功能:
        - 根据字段元数据确定字段类型
        - 使用相应的格式化函数处理字段值
        - 返回格式化结果的数据类
    """
    kind = _resolve_kind(field_meta)
    if kind == "text":
        return FieldFormatResult(text=_safe_text(value), field_type=kind, status="success")
    if kind == "number":
        text, status = _format_number(value)
        return FieldFormatResult(text=text, field_type=kind, status=status)
    if kind == "currency":
        text, status = _format_currency(value)
        return FieldFormatResult(text=text, field_type=kind, status=status)
    if kind == "datetime":
        text, status = _format_datetime(value)
        return FieldFormatResult(text=text, field_type=kind, status=status)
    if kind == "single_select":
        return FieldFormatResult(text=_format_select(value), field_type=kind, status="success")
    if kind == "multi_select":
        text, status = _format_multi_select(value)
        return FieldFormatResult(text=text, field_type=kind, status=status)
    if kind == "person":
        text, status = _format_person(value)
        return FieldFormatResult(text=text, field_type=kind, status=status)
    if kind == "bool":
        text, status = _format_bool(value)
        return FieldFormatResult(text=text, field_type=kind, status=status)
    if kind == "attachment":
        text, status = _format_attachment(value)
        return FieldFormatResult(text=text, field_type=kind, status=status)
    return FieldFormatResult(text=_safe_text(value), field_type="unknown", status="fallback")
