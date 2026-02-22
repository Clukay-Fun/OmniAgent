from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


_LOCAL_TZ = timezone(timedelta(hours=8))
_FIELD_TYPE_TEXT = {1}
_FIELD_TYPE_NUMBER = {2}
_FIELD_TYPE_SINGLE_SELECT = {3}
_FIELD_TYPE_DATE = {5, 6, 23, 1003}
_FIELD_TYPE_BOOL = {7}
_FIELD_TYPE_PERSON = {11, 1001, 1002}
_FIELD_TYPE_ATTACHMENT = {17}


@dataclass(frozen=True)
class FieldFormatResult:
    text: str
    field_type: str
    status: str


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _to_decimal(value: Any) -> Decimal | None:
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
    number = _to_decimal(value)
    if number is None:
        return _safe_text(value), "malformed"
    normalized = number.normalize()
    if normalized == normalized.to_integral():
        return format(int(normalized), ","), "success"
    text = f"{number:,.2f}".rstrip("0").rstrip(".")
    return text, "success"


def _format_currency(value: Any) -> tuple[str, str]:
    number = _to_decimal(value)
    if number is None:
        return _safe_text(value), "malformed"
    return f"\u00a5{number:,.2f}", "success"


def _parse_datetime(value: Any) -> datetime | None:
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
    dt = _parse_datetime(value)
    if dt is None:
        return _safe_text(value), "malformed"
    local = dt.astimezone(_LOCAL_TZ)
    return local.strftime("%Y年%m月%d日 %H:%M"), "success"


def _format_select(value: Any) -> str:
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


def _format_person(value: Any) -> tuple[str, str]:
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
    if isinstance(value, bool):
        return ("✅" if value else "❌"), "success"
    normalized = _safe_text(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return "✅", "success"
    if normalized in {"0", "false", "no", "n", "off", ""}:
        return "❌", "success"
    return _safe_text(value), "malformed"


def _format_attachment(value: Any) -> tuple[str, str]:
    def _extract_name(item: Any) -> str:
        if isinstance(item, dict):
            return _safe_text(item.get("name") or item.get("file_name") or item.get("filename"))
        return _safe_text(item)

    if isinstance(value, list):
        names = [_extract_name(item) for item in value]
        names = [name for name in names if name]
        if not names:
            return "", "malformed"
        return "、".join([f"📎 {name}" for name in names]), "success"

    name = _extract_name(value)
    if not name:
        return _safe_text(value), "malformed"
    return f"📎 {name}", "success"


def _is_currency(field_meta: dict[str, Any] | None) -> bool:
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
    if field_type in _FIELD_TYPE_PERSON:
        return "person"
    if field_type in _FIELD_TYPE_BOOL:
        return "bool"
    if field_type in _FIELD_TYPE_ATTACHMENT:
        return "attachment"
    return "unknown"


def format_field_value(value: Any, field_meta: dict[str, Any] | None = None) -> FieldFormatResult:
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
