from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.feishu.card_template_registry import CardTemplateRegistry


def test_render_query_list_v1() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.list",
        version="v1",
        params={
            "title": "查询结果",
            "total": 2,
            "records": [
                {"fields_text": {"案号": "A-1", "法院": "一审"}},
                {"fields_text": {"案号": "A-2", "法院": "二审"}},
            ],
        },
    )

    assert len(elements) >= 2
    assert elements[0]["tag"] == "markdown"
    assert "共 2 条" in elements[0]["content"]


def test_render_query_detail_v1() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.detail",
        version="v1",
        params={"record": {"fields_text": {"案号": "A-1", "原告": "张三"}}},
    )

    assert len(elements) >= 2
    assert "案号" in elements[1]["content"]


def test_render_action_confirm_v1() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="action.confirm",
        version="v1",
        params={"message": "确认删除", "action": "delete_record"},
    )

    assert "确认删除" in elements[1]["content"]
    assert "delete_record" in elements[1]["content"]


def test_render_error_notice_v1() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="error.notice",
        version="v1",
        params={"message": "权限不足", "skill_name": "DeleteSkill"},
    )

    assert "权限不足" in elements[0]["content"]
    assert "DeleteSkill" in elements[0]["content"]


def test_render_todo_reminder_v1() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="todo.reminder",
        version="v1",
        params={
            "message": "提醒创建成功",
            "content": "提交材料",
            "remind_time": "2026-02-23 10:00",
        },
    )

    assert "提醒创建成功" in elements[0]["content"]
    assert "提交材料" in elements[0]["content"]


def test_render_create_success_v1() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="create.success",
        version="v1",
        params={
            "record": {
                "record_id": "rec_001",
                "fields_text": {"案号": "A-1", "委托人": "张三"},
                "record_url": "https://example.com/rec_001",
            }
        },
    )

    assert "创建成功" in elements[0]["content"]
    assert "案号" in elements[1]["content"]
    assert "查看原记录" in elements[2]["content"]


def test_render_update_success_v1() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="update.success",
        version="v1",
        params={
            "changes": [
                {"field": "状态", "old": "待办", "new": "已完成"},
                {"field": "负责人", "old": "张三", "new": "李四"},
            ],
            "record_id": "rec_002",
            "record_url": "https://example.com/rec_002",
        },
    )

    assert "状态" in elements[1]["content"]
    assert "待办 -> 已完成" in elements[1]["content"]
    assert "查看原记录" in elements[2]["content"]


def test_render_delete_confirm_v1() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="delete.confirm",
        version="v1",
        params={
            "summary": {"案号": "A-3", "记录 ID": "rec_003"},
            "actions": {
                "confirm": {"callback_action": "delete_record_confirm", "intent": "confirm"},
                "cancel": {"callback_action": "delete_record_cancel", "intent": "cancel"},
            },
        },
    )

    assert "高风险操作" in elements[0]["content"]
    assert "案号" in elements[2]["content"]
    assert elements[3]["tag"] == "action"
    assert elements[3]["actions"][0]["text"]["content"] == "确认删除"
    assert elements[3]["actions"][1]["text"]["content"] == "取消"


def test_render_delete_result_cards_v1() -> None:
    registry = CardTemplateRegistry()

    success = registry.render(
        template_id="delete.success",
        version="v1",
        params={"message": "已删除案件 A-4"},
    )
    cancelled = registry.render(
        template_id="delete.cancelled",
        version="v1",
        params={"message": "已取消本次删除"},
    )

    assert "删除成功" in success[0]["content"]
    assert "已取消" in cancelled[0]["content"]


def test_render_error_notice_v1_with_error_class_guidance() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="error.notice",
        version="v1",
        params={
            "message": "当前账号权限不足，无法删除",
            "error_class": "permission_denied",
        },
    )

    assert "权限不足" in elements[0]["content"]
    assert "建议下一步" in elements[0]["content"]
