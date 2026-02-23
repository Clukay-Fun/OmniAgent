from pathlib import Path
import sys
from typing import Optional

import pytest


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.feishu.formatter import CardBuildError, FeishuFormatter
from src.core.response.models import Block, RenderedResponse
from src.core.response.renderer import ResponseRenderer


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


def test_format_supports_query_list_v2_template() -> None:
    formatter = FeishuFormatter(card_enabled=True)
    rendered = RenderedResponse.model_validate(
        {
            "text_fallback": "查询结果",
            "blocks": [{"type": "paragraph", "content": {"text": "旧块"}}],
            "card_template": {
                "template_id": "query.list",
                "version": "v2",
                "params": {
                    "title": "案件查询结果",
                    "total": 2,
                    "records": [{"fields_text": {"案号": "A-1"}}, {"fields_text": {"案号": "A-2"}}],
                    "actions": {
                        "next_page": {"callback_action": "query_list_next_page"},
                        "today_hearing": {"callback_action": "query_list_today_hearing"},
                        "week_hearing": {"callback_action": "query_list_week_hearing"},
                    },
                },
            },
        }
    )

    payload = formatter.format(rendered)

    assert payload["msg_type"] == "interactive"
    assert payload["card"]["elements"][-1]["tag"] == "action"


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


def test_format_crud_templates_from_renderer_skill_results() -> None:
    renderer = ResponseRenderer()
    formatter = FeishuFormatter(card_enabled=True)

    create_rendered = renderer.render(
        {
            "success": True,
            "skill_name": "CreateSkill",
            "reply_text": "创建成功",
            "data": {
                "record_id": "rec_create",
                "record_url": "https://example.com/rec_create",
                "fields": {"案号": "A-1", "委托人": "张三"},
            },
        }
    )
    update_rendered = renderer.render(
        {
            "success": True,
            "skill_name": "UpdateSkill",
            "reply_text": "更新成功",
            "data": {
                "updated_fields": {"状态": "已完成"},
                "source_fields": {"状态": "待办"},
                "record_url": "https://example.com/rec_update",
            },
        }
    )
    delete_confirm_rendered = renderer.render(
        {
            "success": True,
            "skill_name": "DeleteSkill",
            "reply_text": "等待确认删除",
            "data": {
                "pending_delete": {"record_id": "rec_delete", "case_no": "A-2", "table_id": "tbl_1"},
            },
        }
    )
    delete_success_rendered = renderer.render(
        {
            "success": True,
            "skill_name": "DeleteSkill",
            "reply_text": "删除成功",
            "data": {"record_id": "rec_delete"},
        }
    )

    create_payload = formatter.format(create_rendered)
    update_payload = formatter.format(update_rendered)
    delete_confirm_payload = formatter.format(delete_confirm_rendered)
    delete_success_payload = formatter.format(delete_success_rendered)

    assert create_payload["msg_type"] == "interactive"
    assert "查看原记录" in create_payload["card"]["elements"][-1]["content"]
    assert update_payload["msg_type"] == "interactive"
    assert "->" in update_payload["card"]["elements"][1]["content"]
    assert delete_confirm_payload["card"]["elements"][-1]["tag"] == "action"
    assert delete_success_payload["msg_type"] == "interactive"
    assert "删除成功" in delete_success_payload["card"]["elements"][0]["content"]


def test_format_falls_back_to_text_when_create_template_build_fails(monkeypatch, caplog) -> None:
    renderer = ResponseRenderer()
    formatter = FeishuFormatter(card_enabled=True)
    rendered = renderer.render(
        {
            "success": True,
            "skill_name": "CreateSkill",
            "reply_text": "创建成功兜底",
            "data": {
                "record_id": "rec_create",
                "record_url": "https://example.com/rec_create",
                "fields": {"案号": "A-1"},
            },
        }
    )

    def raise_render(*_args, **_kwargs):
        raise ValueError("broken")

    monkeypatch.setattr(formatter._template_registry, "render", raise_render)

    payload = formatter.format(rendered)

    assert "template render failed" in caplog.text
    assert payload == {
        "msg_type": "text",
        "content": {"text": "创建成功兜底"},
    }
