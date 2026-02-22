from __future__ import annotations

from typing import Any


TEMPLATE_DEFAULT_VERSIONS: dict[str, str] = {
    "query.list": "v1",
    "query.detail": "v1",
    "action.confirm": "v1",
    "error.notice": "v1",
    "todo.reminder": "v1",
    "upload.result": "v1",
}


TEMPLATE_ENABLED: dict[str, bool] = {
    "query.list.v1": True,
    "query.detail.v1": True,
    "action.confirm.v1": True,
    "error.notice.v1": True,
    "todo.reminder.v1": True,
    "upload.result.v1": True,
}


def resolve_template_version(template_id: str, version: str | None = None) -> str:
    resolved = (version or "").strip()
    if resolved:
        return resolved
    return TEMPLATE_DEFAULT_VERSIONS.get(template_id, "v1")


def is_template_enabled(template_id: str, version: str) -> bool:
    return bool(TEMPLATE_ENABLED.get(f"{template_id}.{version}", False))


def extract_template_spec(payload: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    raw = payload.get("card_template")
    if not isinstance(raw, dict):
        return None

    template_id = str(raw.get("template_id") or "").strip()
    if not template_id:
        return None

    version = resolve_template_version(template_id, str(raw.get("version") or "").strip())
    params = raw.get("params")
    if not isinstance(params, dict):
        params = {}
    return template_id, version, params
