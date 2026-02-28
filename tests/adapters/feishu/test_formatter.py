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


def _card_elements(payload: dict) -> list[dict]:
    card_raw = payload.get("card")
    card = card_raw if isinstance(card_raw, dict) else {}
    body_raw = card.get("body")
    body = body_raw if isinstance(body_raw, dict) else {}
    elements_raw = body.get("elements")
    if isinstance(elements_raw, list):
        return elements_raw
    fallback_raw = card.get("elements")
    return fallback_raw if isinstance(fallback_raw, list) else []


def _card_markdown_text(payload: dict) -> str:
    texts: list[str] = []

    def _collect(items: list[dict]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            tag = item.get("tag")
            if tag == "markdown":
                content = item.get("content")
                if isinstance(content, str):
                    texts.append(content)
                continue
            if tag == "column_set":
                columns_raw = item.get("columns")
                columns = columns_raw if isinstance(columns_raw, list) else []
                for column in columns:
                    if not isinstance(column, dict):
                        continue
                    column_elements_raw = column.get("elements")
                    column_elements = column_elements_raw if isinstance(column_elements_raw, list) else []
                    _collect([entry for entry in column_elements if isinstance(entry, dict)])

    _collect(_card_elements(payload))
    return "\n".join(texts)


def _card_button_texts(payload: dict) -> list[str]:
    texts: list[str] = []

    def _collect(items: list[dict]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            tag = item.get("tag")
            if tag == "button":
                text_raw = item.get("text")
                text = text_raw if isinstance(text_raw, dict) else {}
                content = text.get("content")
                if isinstance(content, str) and content:
                    texts.append(content)
                continue
            if tag == "action":
                actions_raw = item.get("actions")
                actions = actions_raw if isinstance(actions_raw, list) else []
                _collect([entry for entry in actions if isinstance(entry, dict)])
                continue
            if tag == "column_set":
                columns_raw = item.get("columns")
                columns = columns_raw if isinstance(columns_raw, list) else []
                for column in columns:
                    if not isinstance(column, dict):
                        continue
                    column_elements_raw = column.get("elements")
                    column_elements = column_elements_raw if isinstance(column_elements_raw, list) else []
                    _collect([entry for entry in column_elements if isinstance(entry, dict)])

    _collect(_card_elements(payload))
    return texts


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


def test_format_returns_text_payload_when_blocks_available() -> None:
    formatter = FeishuFormatter(card_enabled=True)
    rendered = RenderedResponse(
        text_fallback="这是一个足够长的回复文本，用于验证在非短文本场景下仍可正常渲染卡片展示，而且不会被短回复策略降级。",
        blocks=[Block(type="paragraph", content={"text": "第一段"})],
    )

    payload = formatter.format(rendered)

    assert payload == {
        "msg_type": "text",
        "content": {"text": "这是一个足够长的回复文本，用于验证在非短文本场景下仍可正常渲染卡片展示，而且不会被短回复策略降级。"},
    }


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
    element = formatter._block_to_element(block)
    assert isinstance(element, dict)
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

    payload = formatter._build_card(rendered)

    assert isinstance(payload, dict)
    elements = _card_elements(payload)
    assert len(elements) == 2
    assert elements[0]["tag"] == "markdown"
    assert "- 有效项" in elements[0]["content"]
    assert elements[1]["tag"] == "markdown"
    assert "- **键**: 值" in elements[1]["content"]


def test_format_falls_back_to_text_when_card_build_raises(monkeypatch, caplog) -> None:
    formatter = FeishuFormatter(card_enabled=True)
    rendered = RenderedResponse.model_validate(
        {
            "text_fallback": "这是一个足够长的兜底文本，用于触发卡片构建分支并记录日志。",
            "card_template": {
                "template_id": "action.confirm",
                "version": "v1",
                "params": {
                    "message": "请确认",
                    "action": "create_record",
                    "payload": {"fields": {"案号": "A-1"}},
                },
            },
            "blocks": [
                {"type": "paragraph", "content": {"text": "请确认"}},
            ],
        }
    )

    def raise_error(*_args, **_kwargs):
        raise CardBuildError("boom")

    monkeypatch.setattr(formatter, "_build_template_card", raise_error)

    payload = formatter.format(rendered)

    assert "fall back to text" in caplog.text
    assert payload == {
        "msg_type": "text",
        "content": {"text": "这是一个足够长的兜底文本，用于触发卡片构建分支并记录日志。"},
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

    assert payload == {
        "msg_type": "text",
        "content": {"text": "模板兜底"},
    }


def test_format_keeps_minimal_confirm_card_interactive() -> None:
    formatter = FeishuFormatter(card_enabled=True)
    rendered = RenderedResponse.model_validate(
        {
            "text_fallback": "请确认",
            "blocks": [{"type": "paragraph", "content": {"text": "请确认"}}],
            "card_template": {
                "template_id": "action.confirm",
                "version": "v1",
                "params": {
                    "message": "请确认是否继续执行",
                    "action": "create_record",
                    "payload": {"fields": {"案号": "A-1"}},
                },
            },
        }
    )

    payload = formatter.format(rendered)

    assert payload["msg_type"] == "interactive"


def test_format_keeps_update_guide_card_interactive() -> None:
    formatter = FeishuFormatter(card_enabled=True)
    rendered = RenderedResponse.model_validate(
        {
            "text_fallback": "请继续提供修改内容",
            "blocks": [{"type": "paragraph", "content": {"text": "请继续提供修改内容"}}],
            "card_template": {
                "template_id": "update.guide",
                "version": "v1",
                "params": {
                    "record_id": "rec_1",
                    "record_case_no": "A-1",
                    "record_identity": "张三 vs 李四",
                },
            },
        }
    )

    payload = formatter.format(rendered)

    assert payload["msg_type"] == "interactive"


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

    assert payload == {
        "msg_type": "text",
        "content": {"text": "查询结果"},
    }


def test_format_falls_back_to_text_when_template_render_fails(caplog) -> None:
    formatter = FeishuFormatter(card_enabled=True)
    rendered = RenderedResponse.model_validate(
        {
            "text_fallback": "这是一个足够长的模板兜底文本，用于验证模板渲染出问题时会记录日志并退回文本。",
            "blocks": [{"type": "paragraph", "content": {"text": "旧块"}}],
            "card_template": {
                "template_id": "action.confirm",
                "version": "v1",
                "params": {},
            },
        }
    )

    payload = formatter.format(rendered)

    assert "template render failed" in caplog.text
    assert payload == {
        "msg_type": "text",
        "content": {"text": "这是一个足够长的模板兜底文本，用于验证模板渲染出问题时会记录日志并退回文本。"},
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

    assert create_payload["msg_type"] == "text"
    assert update_payload["msg_type"] == "text"
    delete_buttons = _card_button_texts(delete_confirm_payload)
    assert "⛔ 确认删除" in delete_buttons
    assert "❌ 取消" in delete_buttons
    assert delete_success_payload["msg_type"] == "text"


def test_format_falls_back_to_text_when_create_template_build_fails(monkeypatch, caplog) -> None:
    formatter = FeishuFormatter(card_enabled=True)
    rendered = RenderedResponse.model_validate(
        {
            "text_fallback": "创建成功兜底",
            "card_template": {
                "template_id": "action.confirm",
                "version": "v1",
                "params": {"message": "确认", "action": "create_record", "payload": {}},
            },
            "blocks": [{"type": "paragraph", "content": {"text": "确认"}}],
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
