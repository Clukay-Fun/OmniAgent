from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.discord.formatter import DiscordFormatter
from src.core.response.models import RenderedResponse


def test_formatter_returns_text_only_by_default() -> None:
    formatter = DiscordFormatter(embed_enabled=False, components_enabled=False)
    rendered = RenderedResponse.model_validate(
        {
            "text_fallback": "纯文本回复",
            "blocks": [{"type": "paragraph", "content": {"text": "纯文本回复"}}],
            "meta": {"skill_name": "ChitchatSkill"},
        }
    )

    payload = formatter.format(rendered)

    assert payload.text == "纯文本回复"
    assert payload.embed is None
    assert payload.components == []


def test_formatter_generates_embed_for_query_skill() -> None:
    formatter = DiscordFormatter(embed_enabled=True, components_enabled=False)
    rendered = RenderedResponse.model_validate(
        {
            "text_fallback": "查询结果",
            "blocks": [
                {"type": "heading", "content": {"text": "案件查询"}},
                {"type": "paragraph", "content": {"text": "共 2 条记录"}},
                {
                    "type": "kv_list",
                    "content": {"items": [{"key": "案号", "value": "A-001"}]},
                },
            ],
            "meta": {"skill_name": "QuerySkill"},
        }
    )

    payload = formatter.format(rendered)

    assert payload.embed is not None
    assert payload.embed.title == "案件查询"
    assert payload.embed.fields
    assert payload.embed.fields[0].name == "案号"
    assert payload.embed.fields[0].value == "A-001"


def test_formatter_query_list_prefers_plain_text_without_embed() -> None:
    formatter = DiscordFormatter(embed_enabled=True, components_enabled=False)
    rendered = RenderedResponse.model_validate(
        {
            "text_fallback": "很长很长的列表文本",
            "blocks": [{"type": "paragraph", "content": {"text": "很长很长的列表文本"}}],
            "meta": {"skill_name": "QuerySkill"},
            "card_template": {
                "template_id": "query.list",
                "version": "v1",
                "params": {
                    "total": 2,
                    "records": [
                        {
                            "fields_text": {
                                "案号": "A-001",
                                "委托人": "甲方",
                                "对方当事人": "乙方",
                                "开庭日": "2026-03-01 09:00",
                                "案件状态": "进行中",
                            }
                        },
                        {
                            "fields_text": {
                                "案号": "A-002",
                                "委托人": "丙方",
                                "对方当事人": "丁方",
                                "开庭日": "2026-03-02 10:00",
                                "案件状态": "待开庭",
                            }
                        },
                    ],
                },
            },
        }
    )

    payload = formatter.format(rendered)

    assert "查询结果" in payload.text
    assert "共 2 条" in payload.text
    assert "**1. A-001**" in payload.text
    assert "👥 甲方 vs 乙方" in payload.text
    assert "**2. A-002**" in payload.text
    assert "\n\n**2. A-002**" in payload.text
    assert payload.embed is None


def test_formatter_query_list_shows_only_five_items_with_navigation_hints() -> None:
    formatter = DiscordFormatter(embed_enabled=True, components_enabled=False)
    records = []
    for idx in range(1, 7):
        records.append(
            {
                "fields_text": {
                    "案号": f"A-00{idx}",
                    "委托人": f"甲方{idx}",
                    "对方当事人": f"乙方{idx}",
                    "开庭日": f"2026-03-0{idx} 09:00",
                    "案件状态": "进行中",
                }
            }
        )
    rendered = RenderedResponse.model_validate(
        {
            "text_fallback": "查询结果",
            "blocks": [{"type": "paragraph", "content": {"text": "查询结果"}}],
            "meta": {"skill_name": "QuerySkill"},
            "card_template": {
                "template_id": "query.list",
                "version": "v1",
                "params": {
                    "total": 12,
                    "records": records,
                },
            },
        }
    )

    payload = formatter.format(rendered)

    assert "本次展示 5 条" in payload.text
    assert "**5. A-005**" in payload.text
    assert "6. A-006" not in payload.text
    assert "第6个详情" in payload.text
    assert "下一页" in payload.text


def test_formatter_generates_confirm_cancel_components() -> None:
    formatter = DiscordFormatter(embed_enabled=False, components_enabled=True)
    rendered = RenderedResponse.model_validate(
        {
            "text_fallback": "请确认",
            "blocks": [{"type": "paragraph", "content": {"text": "请确认"}}],
            "card_template": {
                "template_id": "action.confirm",
                "version": "v1",
                "params": {
                    "action": "create_record",
                    "confirm_text": "确认执行",
                    "cancel_text": "取消",
                    "actions": {
                        "confirm": {"callback_action": "create_record_confirm"},
                        "cancel": {"callback_action": "create_record_cancel"},
                    },
                },
            },
        }
    )

    payload = formatter.format(rendered)

    assert len(payload.components) == 2
    assert payload.components[0].custom_id == "omni:action:create_record_confirm"
    assert payload.components[1].custom_id == "omni:action:create_record_cancel"


def test_formatter_generates_cancel_component_for_update_guide() -> None:
    formatter = DiscordFormatter(embed_enabled=False, components_enabled=True)
    rendered = RenderedResponse.model_validate(
        {
            "text_fallback": "请补充要修改的字段",
            "blocks": [{"type": "paragraph", "content": {"text": "请补充要修改的字段"}}],
            "card_template": {
                "template_id": "update.guide",
                "version": "v1",
                "params": {
                    "cancel_action": {
                        "callback_action": "update_collect_fields_cancel",
                    },
                    "cancel_text": "取消修改",
                },
            },
        }
    )

    payload = formatter.format(rendered)

    assert len(payload.components) == 1
    assert payload.components[0].custom_id == "omni:action:update_collect_fields_cancel"
    assert payload.components[0].label == "取消修改"
