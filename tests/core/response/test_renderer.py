from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.response.renderer import ResponseRenderer


def build_renderer() -> ResponseRenderer:
    return ResponseRenderer(
        templates={
            "success": "成功处理 {skill_name}",
            "failure": "处理失败：{skill_name}",
        },
        assistant_name="小敬",
    )


def test_render_success_with_data_outputs_kv_list_and_non_empty_fallback():
    renderer = build_renderer()

    response = renderer.render(
        {
            "success": True,
            "skill_name": "planner",
            "message": "完成任务",
            "data": {"count": 2, "status": "ok"},
        }
    )

    assert response.text_fallback.strip() != ""
    kv_blocks = [block for block in response.blocks if block.type == "kv_list"]
    assert len(kv_blocks) == 1
    assert kv_blocks[0].content["items"] == [
        {"key": "count", "value": "2"},
        {"key": "status", "value": "ok"},
    ]


def test_render_failure_uses_failure_template_and_assistant_identity():
    renderer = build_renderer()

    response = renderer.render({"success": False, "skill_name": "executor"})

    assert response.text_fallback == "处理失败：executor"
    assert response.meta["assistant_name"] == "小敬"
    assert response.card_template is not None
    assert response.card_template.template_id == "error.notice"


def test_render_prefers_reply_text_for_text_fallback():
    renderer = build_renderer()

    response = renderer.render(
        {
            "success": True,
            "skill_name": "search",
            "message": "来自 message",
            "reply_text": "来自 reply_text",
        }
    )

    assert response.text_fallback == "来自 reply_text"


def test_render_query_skill_selects_list_template_for_multi_records() -> None:
    renderer = build_renderer()

    response = renderer.render(
        {
            "success": True,
            "skill_name": "QuerySkill",
            "reply_text": "查询完成",
            "data": {
                "records": [{"fields_text": {"案号": "A-1"}}, {"fields_text": {"案号": "A-2"}}],
                "total": 2,
            },
        }
    )

    assert response.card_template is not None
    assert response.card_template.template_id == "query.list"


def test_render_always_contains_paragraph_block():
    renderer = build_renderer()

    response = renderer.render({"success": True, "skill_name": "router"})

    assert len(response.blocks) >= 1
    assert response.blocks[0].type == "paragraph"


def test_render_create_skill_selects_create_success_template() -> None:
    renderer = build_renderer()

    response = renderer.render(
        {
            "success": True,
            "skill_name": "CreateSkill",
            "reply_text": "创建成功",
            "data": {
                "record_id": "rec_new",
                "record_url": "https://example.com/rec_new",
                "fields": {"案号": "A-100", "委托人": "张三"},
            },
        }
    )

    assert response.card_template is not None
    assert response.card_template.template_id == "create.success"
    assert response.card_template.params["record_url"] == "https://example.com/rec_new"


def test_render_update_skill_selects_update_success_template() -> None:
    renderer = build_renderer()

    response = renderer.render(
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

    assert response.card_template is not None
    assert response.card_template.template_id == "update.success"
    assert response.card_template.params["changes"][0] == {"field": "状态", "old": "待办", "new": "已完成"}


def test_render_delete_skill_selects_delete_confirm_template() -> None:
    renderer = build_renderer()

    response = renderer.render(
        {
            "success": True,
            "skill_name": "DeleteSkill",
            "reply_text": "等待确认删除",
            "data": {
                "pending_delete": {"record_id": "rec_del", "case_no": "A-200", "table_id": "tbl_1"},
                "records": [{"fields_text": {"案号": "A-200", "案由": "合同纠纷"}}],
            },
        }
    )

    assert response.card_template is not None
    assert response.card_template.template_id == "delete.confirm"
    actions = response.card_template.params["actions"]
    assert actions["confirm"]["callback_action"] == "delete_record_confirm"
    assert actions["cancel"]["callback_action"] == "delete_record_cancel"


def test_render_delete_skill_selects_delete_cancelled_template() -> None:
    renderer = build_renderer()

    response = renderer.render(
        {
            "success": True,
            "skill_name": "DeleteSkill",
            "reply_text": "好的，已取消删除操作。",
            "data": {},
        }
    )

    assert response.card_template is not None
    assert response.card_template.template_id == "delete.cancelled"


def test_render_failure_classifies_error_type() -> None:
    renderer = build_renderer()

    response = renderer.render(
        {
            "success": False,
            "skill_name": "DeleteSkill",
            "message": "当前账号权限不足，无法执行删除",
        }
    )

    assert response.card_template is not None
    assert response.card_template.template_id == "error.notice"
    assert response.card_template.params["error_class"] == "permission_denied"


def test_load_templates_falls_back_to_defaults_when_file_missing(tmp_path: Path):
    missing_path = tmp_path / "missing.yaml"
    renderer = ResponseRenderer(templates_path=missing_path)

    response = renderer.render({"success": True, "skill_name": "router"})

    assert response.text_fallback == "已完成 router"


def test_load_templates_falls_back_to_defaults_when_yaml_invalid(tmp_path: Path):
    broken_path = tmp_path / "responses.yaml"
    broken_path.write_text("success: [invalid", encoding="utf-8")
    renderer = ResponseRenderer(templates_path=broken_path)

    response = renderer.render({"success": False, "skill_name": "executor"})

    assert response.text_fallback == "处理失败：executor"
