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
