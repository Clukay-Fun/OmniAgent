from pathlib import Path
import sys
from typing import Optional

import pytest


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.feishu.formatter import CardBuildError, FeishuFormatter
from src.core.response.models import Block, RenderedResponse


def test_format_returns_text_payload_when_card_disabled() -> None:
    formatter = FeishuFormatter(card_enabled=False)
    rendered = RenderedResponse(
        text_fallback="纯文本兜底",
        blocks=[Block(type="paragraph", content={"text": "ignored"})],
    )

    payload = formatter.format(rendered)

    assert payload == {
        "msg_type": "text",
        "content": {"text": "纯文本兜底"},
    }


def test_format_returns_interactive_card_when_blocks_available() -> None:
    formatter = FeishuFormatter(card_enabled=True)
    rendered = RenderedResponse(
        text_fallback="fallback",
        blocks=[Block(type="paragraph", content={"text": "第一段"})],
    )

    payload = formatter.format(rendered)

    assert payload["msg_type"] == "interactive"
    assert isinstance(payload["card"], dict)
    assert payload["card"]["elements"]


@pytest.mark.parametrize(
    ("block", "expected_tag", "expected_content"),
    [
        (Block(type="paragraph", content={"text": "第一段"}), "markdown", "第一段"),
        (Block(type="heading", content={"text": "标题"}), "markdown", "**标题**"),
        (
            Block(type="bullet_list", content={"items": ["A", "  ", "B"]}),
            "markdown",
            "- A\n- B",
        ),
        (
            Block(type="kv_list", content={"items": [{"key": "k", "value": "v"}]}),
            "markdown",
            "- **k**: v",
        ),
        (Block(type="callout", content={"text": "提醒"}), "markdown", "> 提醒"),
        (Block(type="divider", content={}), "hr", None),
    ],
)
def test_format_maps_block_types_to_card_elements(
    block: Block,
    expected_tag: str,
    expected_content: Optional[str],
) -> None:
    formatter = FeishuFormatter(card_enabled=True)
    rendered = RenderedResponse(text_fallback="fallback", blocks=[block])

    payload = formatter.format(rendered)

    assert payload["msg_type"] == "interactive"
    element = payload["card"]["elements"][0]
    assert element["tag"] == expected_tag
    if expected_content is None:
        assert "content" not in element
    else:
        assert expected_content in element["content"]


def test_format_filters_empty_content_and_invalid_items() -> None:
    formatter = FeishuFormatter(card_enabled=True)
    rendered = RenderedResponse(
        text_fallback="filtered fallback",
        blocks=[
            Block(type="paragraph", content={"text": "   "}),
            Block(type="heading", content={"text": ""}),
            Block(type="bullet_list", content={"items": "not-a-list"}),
            Block(type="bullet_list", content={"items": [" ", "有效项"]}),
            Block(type="kv_list", content={"items": "not-a-list"}),
            Block(
                type="kv_list",
                content={
                    "items": [
                        "bad-item",
                        {"key": " ", "value": " "},
                        {"key": "键", "value": "值"},
                    ]
                },
            ),
            Block(type="callout", content={"text": "   "}),
        ],
    )

    payload = formatter.format(rendered)

    assert payload["msg_type"] == "interactive"
    elements = payload["card"]["elements"]
    assert len(elements) == 2
    assert elements[0]["tag"] == "markdown"
    assert "- 有效项" in elements[0]["content"]
    assert elements[1]["tag"] == "markdown"
    assert "- **键**: 值" in elements[1]["content"]


def test_format_falls_back_to_text_when_card_build_raises(monkeypatch, caplog) -> None:
    formatter = FeishuFormatter(card_enabled=True)
    rendered = RenderedResponse(
        text_fallback="异常兜底",
        blocks=[Block(type="paragraph", content={"text": "第一段"})],
    )

    def raise_error(_rendered: RenderedResponse):
        raise CardBuildError("boom")

    monkeypatch.setattr(formatter, "_build_card", raise_error)

    payload = formatter.format(rendered)

    assert "fall back to text" in caplog.text
    assert payload == {
        "msg_type": "text",
        "content": {"text": "异常兜底"},
    }


def test_format_uses_template_registry_when_card_template_present() -> None:
    formatter = FeishuFormatter(card_enabled=True)
    rendered = RenderedResponse.model_validate(
        {
            "text_fallback": "模板兜底",
            "blocks": [{"type": "paragraph", "content": {"text": "旧块"}}],
            "card_template": {
                "template_id": "error.notice",
                "version": "v1",
                "params": {"message": "模板错误提示", "title": "异常"},
            },
        }
    )

    payload = formatter.format(rendered)

    assert payload["msg_type"] == "interactive"
    assert "模板错误提示" in payload["card"]["elements"][0]["content"]


def test_format_falls_back_to_text_when_template_render_fails(caplog) -> None:
    formatter = FeishuFormatter(card_enabled=True)
    rendered = RenderedResponse.model_validate(
        {
            "text_fallback": "模板失败兜底",
            "blocks": [{"type": "paragraph", "content": {"text": "旧块"}}],
            "card_template": {
                "template_id": "query.list",
                "version": "v1",
                "params": {},
            },
        }
    )

    payload = formatter.format(rendered)

    assert "template render failed" in caplog.text
    assert payload == {
        "msg_type": "text",
        "content": {"text": "模板失败兜底"},
    }
