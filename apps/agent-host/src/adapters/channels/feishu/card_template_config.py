from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


logger = logging.getLogger(__name__)


BUILTIN_TEMPLATE_DEFAULT_VERSIONS: dict[str, str] = {
    "query.list": "v1",
    "query.detail": "v1",
    "action.confirm": "v1",
    "error.notice": "v1",
    "todo.reminder": "v1",
    "upload.result": "v1",
    "create.success": "v1",
    "update.success": "v1",
    "delete.confirm": "v1",
    "delete.success": "v1",
    "delete.cancelled": "v1",
}


BUILTIN_TEMPLATE_ENABLED: dict[str, bool] = {
    "query.list.v1": True,
    "query.detail.v1": True,
    "action.confirm.v1": True,
    "error.notice.v1": True,
    "todo.reminder.v1": True,
    "upload.result.v1": True,
    "create.success.v1": True,
    "update.success.v1": True,
    "delete.confirm.v1": True,
    "delete.success.v1": True,
    "delete.cancelled.v1": True,
}


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[4] / "config" / "card_templates.yaml"


def _yaml_enabled() -> bool:
    raw = os.getenv("CARD_TEMPLATE_CONFIG_YAML_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _config_path() -> Path:
    custom_path = os.getenv("CARD_TEMPLATE_CONFIG_PATH", "").strip()
    return Path(custom_path) if custom_path else _default_config_path()


def _normalize_default_versions(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    output: dict[str, str] = {}
    for key, value in raw.items():
        template_id = str(key or "").strip()
        version = str(value or "").strip()
        if template_id and version:
            output[template_id] = version
    return output


def _normalize_enabled(raw: Any) -> dict[str, bool]:
    if not isinstance(raw, dict):
        return {}
    output: dict[str, bool] = {}
    for key, value in raw.items():
        template_key = str(key or "").strip()
        if not template_key:
            continue
        if isinstance(value, bool):
            output[template_key] = value
            continue
        text = str(value or "").strip().lower()
        output[template_key] = text in {"1", "true", "yes", "on"}
    return output


@lru_cache(maxsize=1)
def _load_template_config() -> tuple[dict[str, str], dict[str, bool]]:
    if not _yaml_enabled():
        return BUILTIN_TEMPLATE_DEFAULT_VERSIONS, BUILTIN_TEMPLATE_ENABLED

    path = _config_path()
    if not path.exists():
        return BUILTIN_TEMPLATE_DEFAULT_VERSIONS, BUILTIN_TEMPLATE_ENABLED

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(
            "card template YAML load failed, fallback to builtins: %s",
            exc,
            extra={"event_code": "feishu.card_template.config_load_failed", "path": str(path)},
        )
        return BUILTIN_TEMPLATE_DEFAULT_VERSIONS, BUILTIN_TEMPLATE_ENABLED

    if not isinstance(data, dict):
        return BUILTIN_TEMPLATE_DEFAULT_VERSIONS, BUILTIN_TEMPLATE_ENABLED

    defaults = _normalize_default_versions(data.get("default_versions"))
    enabled = _normalize_enabled(data.get("enabled"))
    if not defaults:
        defaults = BUILTIN_TEMPLATE_DEFAULT_VERSIONS
    if not enabled:
        enabled = BUILTIN_TEMPLATE_ENABLED
    return defaults, enabled


def reset_template_config_cache() -> None:
    _load_template_config.cache_clear()


def resolve_template_version(template_id: str, version: str | None = None) -> str:
    resolved = (version or "").strip()
    if resolved:
        return resolved
    default_versions, _ = _load_template_config()
    return default_versions.get(template_id, "v1")


def is_template_enabled(template_id: str, version: str) -> bool:
    _, enabled = _load_template_config()
    return bool(enabled.get(f"{template_id}.{version}", False))


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
