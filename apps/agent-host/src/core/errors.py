"""
Typed core errors.

每个错误类都有 code 字段对应 error_messages.yaml 里的用户文案。
上层通过 error.code 查 catalog 取得本地化文案，再交给 renderer 渲染。
"""

from __future__ import annotations


class CoreError(Exception):
    """Base class for all typed core errors."""
    code: str = "unknown_error"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        if code:
            self.code = code
        super().__init__(message or self.code)


class PendingActionExpiredError(CoreError):
    code = "pending_action_expired"


class PendingActionNotFoundError(CoreError):
    code = "pending_action_not_found"


class LocatorTripletMissingError(CoreError):
    code = "locator_triplet_missing"


class CallbackDuplicatedError(CoreError):
    code = "callback_duplicated"


class WritePermissionDeniedError(CoreError):
    code = "write_permission_denied"


# ── error catalog loader ────────────────────────────────────────────

from pathlib import Path
from typing import Any

import yaml


_ERROR_CATALOG: dict[str, str] | None = None


def _load_error_catalog() -> dict[str, str]:
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
    """Resolve user-facing message by error code."""
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
    """Resolve user-facing message for a typed error from the YAML catalog."""
    return get_user_message_by_code(error.code, fallback=str(error))
