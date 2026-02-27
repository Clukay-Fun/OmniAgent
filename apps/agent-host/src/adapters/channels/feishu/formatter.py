"""
描述: 该模块负责将渲染后的响应格式化为飞书消息卡片或纯文本。
主要功能:
    - 根据配置和条件选择生成飞书消息卡片或纯文本
    - 处理模板卡片的渲染和错误处理
    - 将渲染后的响应块转换为飞书消息卡片元素
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from src.adapters.channels.feishu.card_template_config import resolve_template_version
from src.adapters.channels.feishu.card_template_registry import (
    CardTemplateRegistry,
    TemplateLookupError,
    TemplateValidationError,
)
from src.adapters.channels.feishu.card_scaffold import build_card_payload, build_text_payload
from src.core.response.models import Block, RenderedResponse
from src.utils.metrics import record_card_template


logger = logging.getLogger(__name__)


class CardBuildError(Exception):
    """自定义异常类，用于表示卡片构建过程中发生的错误。"""
    pass


class FeishuFormatter:
    """
    飞书消息格式化器，负责将渲染后的响应格式化为飞书消息卡片或纯文本。

    功能:
        - 根据配置和条件选择生成飞书消息卡片或纯文本
        - 处理模板卡片的渲染和错误处理
        - 将渲染后的响应块转换为飞书消息卡片元素
    """

    def __init__(self, card_enabled: bool = True) -> None:
        """
        初始化飞书格式化器。

        参数:
            card_enabled (bool): 是否启用卡片功能，默认为 True。
        """
        self._card_enabled = card_enabled
        self._template_registry = CardTemplateRegistry()
        self._short_text_max_chars = 36

    def format(self, rendered: RenderedResponse, *, prefer_card: bool = False) -> Dict[str, Any]:
        """
        格式化渲染后的响应为飞书消息卡片或纯文本。

        参数:
            rendered (RenderedResponse): 渲染后的响应对象。
            prefer_card (bool): 是否优先使用卡片格式，默认为 False。

        返回:
            Dict[str, Any]: 格式化后的飞书消息卡片或纯文本。
        """
        if not self._card_enabled:
            return self._text_payload(rendered)

        if not prefer_card and self._should_force_text(rendered):
            return self._text_payload(rendered)

        if rendered.card_template is not None:
            template_id = rendered.card_template.template_id
            version = resolve_template_version(template_id, rendered.card_template.version)
            params = rendered.card_template.params
            try:
                template_card = self._build_template_card(template_id, version, params)
                if template_card is not None:
                    record_card_template(f"{template_id}.{version}", "success")
                    return template_card
                record_card_template(f"{template_id}.{version}", "empty")
                return self._text_payload(rendered)
            except (TemplateLookupError, TemplateValidationError, CardBuildError, ValueError, TypeError) as exc:
                logger.warning(
                    "Feishu template render failed, fall back to text: %s",
                    exc,
                    extra={"event_code": "feishu.card_template.render_failed", "template_id": template_id, "version": version},
                )
                record_card_template(f"{template_id}.{version}", "failed")
                return self._text_payload(rendered)

        try:
            card_payload = self._build_card(rendered)
        except CardBuildError as exc:
            logger.warning("Feishu card build failed, fall back to text: %s", exc)
            return self._text_payload(rendered)

        if card_payload is None:
            return self._text_payload(rendered)
        return card_payload

    def _should_force_text(self, rendered: RenderedResponse) -> bool:
        """
        判断是否强制使用纯文本格式。

        参数:
            rendered (RenderedResponse): 渲染后的响应对象。

        返回:
            bool: 是否强制使用纯文本格式。
        """
        text = rendered.text_fallback.strip()
        if not text:
            return True
        if self._is_error_like_text(text):
            return True
        if rendered.card_template is not None:
            return False
        if len(text) <= self._short_text_max_chars and self._is_simple_paragraph_reply(rendered.blocks):
            return True
        return False

    def _is_error_like_text(self, text: str) -> bool:
        """
        判断文本是否包含错误相关的关键词。

        参数:
            text (str): 要检查的文本。

        返回:
            bool: 文本是否包含错误相关的关键词。
        """
        normalized = text.lower()
        keywords = (
            "错误",
            "失败",
            "超时",
            "异常",
            "抱歉",
            "请稍后重试",
            "未找到",
            "error",
            "failed",
            "timeout",
        )
        return any(item in normalized for item in keywords)

    def _is_simple_paragraph_reply(self, blocks: List[Block]) -> bool:
        """
        判断响应块是否为简单的段落回复。

        参数:
            blocks (List[Block]): 响应块列表。

        返回:
            bool: 响应块是否为简单的段落回复。
        """
        if len(blocks) != 1:
            return False
        block = blocks[0]
        return block.type == "paragraph"

    def _build_template_card(self, template_id: str, version: str, params: dict[str, Any]) -> Dict[str, Any] | None:
        """
        构建模板卡片。

        参数:
            template_id (str): 模板ID。
            version (str): 模板版本。
            params (dict[str, Any]): 模板参数。

        返回:
            Dict[str, Any] | None: 构建的模板卡片或 None。
        """
        rendered = self._template_registry.render(template_id=template_id, version=version, params=params)
        if isinstance(rendered, dict):
            elements_raw = rendered.get("elements")
            elements = [item for item in elements_raw if isinstance(item, dict)] if isinstance(elements_raw, list) else []
            wrapper = rendered.get("wrapper") if isinstance(rendered.get("wrapper"), dict) else None
            return build_card_payload(elements, wrapper=wrapper)

        elements = rendered if isinstance(rendered, list) else []
        return build_card_payload([item for item in elements if isinstance(item, dict)])

    def _text_payload(self, rendered: RenderedResponse) -> Dict[str, Any]:
        """
        构建纯文本消息。

        参数:
            rendered (RenderedResponse): 渲染后的响应对象。

        返回:
            Dict[str, Any]: 构建的纯文本消息。
        """
        return build_text_payload(rendered.text_fallback)

    def _build_card(self, rendered: RenderedResponse) -> Dict[str, Any] | None:
        """
        构建飞书消息卡片。

        参数:
            rendered (RenderedResponse): 渲染后的响应对象。

        返回:
            Dict[str, Any] | None: 构建的飞书消息卡片或 None。
        """
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

        return build_card_payload(elements)

    def _block_to_element(self, block: Block) -> Dict[str, Any] | None:
        """
        将响应块转换为飞书消息卡片元素。

        参数:
            block (Block): 响应块。

        返回:
            Dict[str, Any] | None: 转换后的飞书消息卡片元素或 None。
        """
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

    def _markdown_element(self, text: str) -> Dict[str, Any] | None:
        """
        构建 Markdown 格式的飞书消息卡片元素。

        参数:
            text (str): Markdown 文本。

        返回:
            Dict[str, Any] | None: 构建的 Markdown 格式的飞书消息卡片元素或 None。
        """
        if not text:
            return None
        return {
            "tag": "markdown",
            "content": text,
        }
