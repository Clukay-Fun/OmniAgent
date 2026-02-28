from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.expression.response.renderer import ResponseRenderer


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


def test_render_query_skill_selects_list_template_v2_when_enabled() -> None:
    renderer = ResponseRenderer(query_card_v2_enabled=True)

    response = renderer.render(
        {
            "success": True,
            "skill_name": "QuerySkill",
            "reply_text": "查询完成",
            "data": {
                "records": [{"fields_text": {"案号": "A-1"}}, {"fields_text": {"案号": "A-2"}}],
                "total": 2,
                "pending_action": {
                    "action": "query_list_navigation",
                    "payload": {
                        "callbacks": {
                            "query_list_next_page": {"callback_action": "query_list_next_page"},
                            "query_list_today_hearing": {"callback_action": "query_list_today_hearing"},
                            "query_list_week_hearing": {"callback_action": "query_list_week_hearing"},
                        }
                    },
                },
            },
        }
    )

    assert response.card_template is not None
    assert response.card_template.template_id == "query.list"
    assert response.card_template.version == "v2"
    assert response.card_template.params["actions"]["next_page"]["callback_action"] == "query_list_next_page"
    assert response.card_template.params["style"] == "T2"
    assert response.card_template.params["domain"] == "case"


def test_render_query_skill_empty_result_uses_query_list_v2_not_found_card() -> None:
    renderer = ResponseRenderer(query_card_v2_enabled=True)

    response = renderer.render(
        {
            "success": True,
            "skill_name": "QuerySkill",
            "reply_text": "未找到相关案件记录",
            "data": {
                "records": [],
                "total": 0,
                "query_meta": {"table_name": "合同管理表", "tool": "data.bitable.search"},
            },
        }
    )

    assert response.card_template is not None
    assert response.card_template.template_id == "query.list"
    assert response.card_template.params["domain"] == "contracts"
    assert response.card_template.params["style"] == "HT-T2"


def test_render_query_skill_selects_table_specific_style() -> None:
    renderer = ResponseRenderer(query_card_v2_enabled=True)

    response = renderer.render(
        {
            "success": True,
            "skill_name": "QuerySkill",
            "reply_text": "查询完成",
            "data": {
                "records": [{"fields_text": {"项目名称": "A"}}, {"fields_text": {"项目名称": "B"}}],
                "total": 2,
                "query_meta": {
                    "table_name": "招投标台账",
                    "table_id": "tbl_bid_001",
                    "tool": "data.bitable.search_person",
                },
            },
        }
    )

    assert response.card_template is not None
    assert response.card_template.template_id == "query.list"
    assert response.card_template.params["domain"] == "bidding"
    assert response.card_template.params["style"] == "ZB-T2"
    assert response.card_template.params["table_name"] == "招投标台账"
    assert response.card_template.params["table_id"] == "tbl_bid_001"


def test_render_query_skill_selects_case_style_variant() -> None:
    renderer = ResponseRenderer(query_card_v2_enabled=True)

    response = renderer.render(
        {
            "success": True,
            "skill_name": "QuerySkill",
            "reply_text": "请给我最近开庭安排",
            "data": {
                "records": [{"fields_text": {"项目 ID": "A-1"}}, {"fields_text": {"项目 ID": "A-2"}}],
                "total": 2,
                "query_meta": {"table_name": "案件项目总库", "tool": "data.bitable.search_date_range"},
            },
        }
    )

    assert response.card_template is not None
    assert response.card_template.params["style"] == "T2"
    assert response.card_template.params["style_variant"] == "T3A"


def test_render_always_contains_paragraph_block():
    renderer = build_renderer()

    response = renderer.render({"success": True, "skill_name": "router"})

    assert len(response.blocks) >= 1
    assert response.blocks[0].type == "paragraph"


def test_render_kv_list_hides_raw_technical_field() -> None:
    renderer = build_renderer()

    response = renderer.render(
        {
            "success": True,
            "skill_name": "SummarySkill",
            "message": "ok",
            "data": {"raw": "leak", "status": "ok"},
        }
    )

    kv_blocks = [block for block in response.blocks if block.type == "kv_list"]
    assert len(kv_blocks) == 1
    assert kv_blocks[0].content["items"] == [{"key": "status", "value": "ok"}]


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
                "table_name": "案件项目总库",
                "fields": {"案号": "A-100", "委托人": "张三"},
            },
        }
    )

    assert response.card_template is not None
    assert response.card_template.template_id == "create.success"
    assert response.card_template.params["record_url"] == "https://example.com/rec_new"
    assert response.card_template.params["table_name"] == "案件项目总库"


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


def test_render_pending_create_action_includes_payload_for_template() -> None:
    renderer = build_renderer()

    response = renderer.render(
        {
            "success": True,
            "skill_name": "CreateSkill",
            "reply_text": "请确认新增",
            "data": {
                "table_name": "案件项目总库",
                "pending_action": {
                    "action": "create_record",
                    "payload": {
                        "table_name": "案件项目总库",
                        "fields": {"案号": "(2026)粤0101民初100号"},
                        "required_fields": ["案号", "委托人"],
                    },
                },
            },
        }
    )

    assert response.card_template is not None
    assert response.card_template.template_id == "action.confirm"
    assert response.card_template.params["action"] == "create_record"
    assert response.card_template.params["payload"]["fields"]["案号"] == "(2026)粤0101民初100号"


def test_render_pending_close_action_uses_close_record_callbacks() -> None:
    renderer = build_renderer()

    response = renderer.render(
        {
            "success": True,
            "skill_name": "UpdateSkill",
            "reply_text": "请确认关闭",
            "data": {
                "table_name": "案件项目总库",
                "pending_action": {
                    "action": "close_record",
                    "payload": {
                        "record_id": "rec_001",
                        "table_name": "案件项目总库",
                        "fields": {"案件状态": "已结案"},
                    },
                },
            },
        }
    )

    assert response.card_template is not None
    assert response.card_template.template_id == "action.confirm"
    actions = response.card_template.params["actions"]
    assert actions["confirm"]["callback_action"] == "close_record_confirm"
    assert actions["cancel"]["callback_action"] == "close_record_cancel"


def test_render_pending_batch_action_includes_retry_callback() -> None:
    renderer = build_renderer()

    response = renderer.render(
        {
            "success": True,
            "skill_name": "UpdateSkill",
            "reply_text": "请确认批量更新",
            "data": {
                "table_name": "案件项目总库",
                "pending_action": {
                    "action": "batch_update_records",
                    "payload": {
                        "operations": [
                            {"record_id": "rec_1", "fields": {"进展": "已联系"}},
                            {"record_id": "rec_2", "fields": {"进展": "已补证"}},
                        ]
                    },
                },
            },
        }
    )

    assert response.card_template is not None
    assert response.card_template.template_id == "action.confirm"
    actions = response.card_template.params["actions"]
    assert actions["confirm"]["callback_action"] == "batch_update_records_confirm"
    assert actions["cancel"]["callback_action"] == "batch_update_records_cancel"
    assert actions["retry"]["callback_action"] == "batch_update_records_retry"


def test_render_pending_update_collect_fields_uses_update_guide_template() -> None:
    renderer = build_renderer()

    response = renderer.render(
        {
            "success": True,
            "skill_name": "UpdateSkill",
            "reply_text": "已定位到案件，请告诉我要修改什么。",
            "data": {
                "record_id": "rec_guide_1",
                "table_type": "case",
                "record_case_no": "JFTD-20260001",
                "record_identity": "香港华艺设计顾问 vs 广州荔富汇景",
                "pending_action": {
                    "action": "update_collect_fields",
                    "payload": {
                        "record_id": "rec_guide_1",
                        "table_type": "case",
                    },
                },
            },
        }
    )

    assert response.card_template is not None
    assert response.card_template.template_id == "update.guide"
    assert response.card_template.params["cancel_action"]["callback_action"] == "update_collect_fields_cancel"


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


def test_render_failure_classifies_record_id_not_found() -> None:
    renderer = build_renderer()

    response = renderer.render(
        {
            "success": False,
            "skill_name": "UpdateSkill",
            "message": "[1254043] RecordIdNotFound",
        }
    )

    assert response.card_template is not None
    assert response.card_template.template_id == "error.notice"
    assert response.card_template.params["error_class"] == "record_not_found"


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


# ── S3: typed error → error catalog → user message ──────────────────

from src.core.foundation.common.errors import (  # noqa: E402
    CoreError,
    PendingActionExpiredError,
    PendingActionNotFoundError,
    LocatorTripletMissingError,
    CallbackDuplicatedError,
    get_user_message_by_code,
    get_user_message,
)


def test_typed_error_has_correct_code() -> None:
    assert PendingActionExpiredError().code == "pending_action_expired"
    assert PendingActionNotFoundError().code == "pending_action_not_found"
    assert LocatorTripletMissingError().code == "locator_triplet_missing"
    assert CallbackDuplicatedError().code == "callback_duplicated"


def test_error_catalog_returns_user_message() -> None:
    msg = get_user_message(PendingActionExpiredError())
    assert "过期" in msg or "超时" in msg or "expired" in msg.lower()


def test_error_catalog_returns_fallback_for_unknown_code() -> None:
    err = CoreError("some error", code="totally_unknown_code_xyz")
    msg = get_user_message(err)
    assert msg  # should return something, not empty


def test_error_catalog_lookup_by_code_falls_back_to_unknown_message() -> None:
    msg = get_user_message_by_code("totally_unknown_code_xyz")
    assert msg == get_user_message_by_code("unknown_error")


def test_renderer_failure_uses_catalog_message_when_error_code_exists() -> None:
    renderer = build_renderer()

    response = renderer.render(
        {
            "success": False,
            "skill_name": "UpdateSkill",
            "message": "inline message should not leak",
            "data": {"error_code": "pending_action_expired"},
        }
    )

    assert "过期" in response.text_fallback or "超时" in response.text_fallback
    assert response.card_template is not None
    assert response.card_template.params["error_code"] == "pending_action_expired"


def test_error_catalog_lookup_by_code_supports_template_kwargs() -> None:
    msg = get_user_message_by_code("delete_record_failed", detail="RecordIdNotFound")
    assert msg == "删除失败：RecordIdNotFound"


def test_error_catalog_lookup_by_code_ignores_missing_template_kwargs() -> None:
    msg = get_user_message_by_code("delete_record_failed")
    assert msg == "删除失败：{detail}"
