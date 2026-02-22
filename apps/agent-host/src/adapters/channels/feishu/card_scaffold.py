from __future__ import annotations

from typing import Any


def build_card_payload(elements: list[dict[str, Any]]) -> dict[str, Any] | None:
    safe_elements = [item for item in elements if isinstance(item, dict)]
    if not safe_elements:
        return None
    return {
        "msg_type": "interactive",
        "card": {
            "elements": safe_elements,
        },
    }


def build_text_payload(text: str) -> dict[str, Any]:
    return {
        "msg_type": "text",
        "content": {"text": str(text or "")},
    }
