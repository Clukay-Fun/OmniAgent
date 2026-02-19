from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.response.models import Block, RenderedResponse


logger = logging.getLogger(__name__)


class CardBuildError(Exception):
    pass


class FeishuFormatter:
    def __init__(self, card_enabled: bool = True) -> None:
        self._card_enabled = card_enabled

    def format(self, rendered: RenderedResponse) -> Dict[str, Any]:
        if not self._card_enabled:
            return self._text_payload(rendered)

        try:
            card_payload = self._build_card(rendered)
        except CardBuildError as exc:
            logger.warning("Feishu card build failed, fall back to text: %s", exc)
            return self._text_payload(rendered)

        if card_payload is None:
            return self._text_payload(rendered)
        return card_payload

    def _text_payload(self, rendered: RenderedResponse) -> Dict[str, Any]:
        return {
            "msg_type": "text",
            "content": {"text": rendered.text_fallback},
        }

    def _build_card(self, rendered: RenderedResponse) -> Optional[Dict[str, Any]]:
        try:
            elements: List[Dict[str, Any]] = []
            for block in rendered.blocks:
                element = self._block_to_element(block)
                if element is not None:
                    elements.append(element)
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise CardBuildError(str(exc)) from exc

        if not elements:
            return None

        return {
            "msg_type": "interactive",
            "card": {
                "elements": elements,
            },
        }

    def _block_to_element(self, block: Block) -> Optional[Dict[str, Any]]:
        content = block.content if isinstance(block.content, dict) else {}

        if block.type == "divider":
            return {"tag": "hr"}

        if block.type == "paragraph":
            text = str(content.get("text", "")).strip()
            return self._markdown_element(text)

        if block.type == "heading":
            text = str(content.get("text", "")).strip()
            if not text:
                return None
            return self._markdown_element(f"**{text}**")

        if block.type == "bullet_list":
            items = content.get("items")
            if not isinstance(items, list):
                return None
            lines = [f"- {str(item).strip()}" for item in items if str(item).strip()]
            return self._markdown_element("\n".join(lines))

        if block.type == "kv_list":
            items = content.get("items")
            if not isinstance(items, list):
                return None

            lines: List[str] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key", "")).strip()
                value = str(item.get("value", "")).strip()
                if key and value:
                    lines.append(f"- **{key}**: {value}")
                elif key:
                    lines.append(f"- **{key}**")
                elif value:
                    lines.append(f"- {value}")
            return self._markdown_element("\n".join(lines))

        if block.type == "callout":
            text = str(content.get("text", "")).strip()
            if not text:
                return None
            return self._markdown_element(f"> {text}")

        return None

    def _markdown_element(self, text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        return {
            "tag": "markdown",
            "content": text,
        }
