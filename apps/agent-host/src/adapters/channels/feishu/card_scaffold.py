from __future__ import annotations

from typing import Any


def build_card_payload(
    elements: list[dict[str, Any]],
    wrapper: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    safe_elements = [item for item in elements if isinstance(item, dict)]
    if not safe_elements:
        return None

    wrapper_data = wrapper if isinstance(wrapper, dict) else {}
    card: dict[str, Any] = {
        "schema": str(wrapper_data.get("schema") or "2.0"),
        "body": {"elements": safe_elements},
    }

    raw_config = wrapper_data.get("config")
    config = dict(raw_config) if isinstance(raw_config, dict) else {}
    config.setdefault("update_multi", True)
    if config:
        card["config"] = config

    for key in ("header", "card_link", "i18n_elements", "i18n_header"):
        value = wrapper_data.get(key)
        if value is not None:
            card[key] = value

    body_raw = wrapper_data.get("body")
    if isinstance(body_raw, dict):
        body = dict(body_raw)
        body["elements"] = safe_elements
        card["body"] = body

    return {
        "msg_type": "interactive",
        "card": card,
    }


def build_text_payload(text: str) -> dict[str, Any]:
    return {
        "msg_type": "text",
        "content": {"text": str(text or "")},
    }
