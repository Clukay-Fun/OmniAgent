from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

# orchestrator imports Postgres client, which requires asyncpg at import time.
sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))
crypto_module = types.ModuleType("Crypto")
crypto_cipher_module = types.ModuleType("Crypto.Cipher")
setattr(crypto_cipher_module, "AES", object())
setattr(crypto_module, "Cipher", crypto_cipher_module)
sys.modules.setdefault("Crypto", crypto_module)
sys.modules.setdefault("Crypto.Cipher", crypto_cipher_module)

from src.api.channels.feishu.webhook_router import _build_send_payload, _pick_reply_text


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


def test_pick_reply_text_prefers_outbound_text_fallback() -> None:
    reply = {
        "text": "reply text",
        "outbound": {"text_fallback": "outbound text"},
    }

    assert _pick_reply_text(reply) == "outbound text"


def test_pick_reply_text_falls_back_to_reply_text_without_outbound() -> None:
    reply = {"text": "reply text"}

    assert _pick_reply_text(reply) == "reply text"


def test_build_send_payload_formats_outbound_into_feishu_payload() -> None:
    reply = {
        "type": "text",
        "text": "fallback text",
        "outbound": {
            "text_fallback": "outbound text",
            "blocks": [{"type": "paragraph", "content": {"text": "卡片正文"}}],
            "meta": {"assistant_name": "测试助手", "skill_name": "QuerySkill"},
        },
    }

    payload = _build_send_payload(reply, card_enabled=True)

    assert payload["msg_type"] == "text"
    assert payload["content"]["text"] == "outbound text"


def test_build_send_payload_uses_card_for_rich_long_response() -> None:
    reply = {
        "type": "text",
        "text": "fallback text",
        "outbound": {
            "text_fallback": "这是一个较长的查询结果回复，用于确认仍然会使用卡片进行展示，避免完全退化为纯文本。",
            "blocks": [
                {"type": "heading", "content": {"text": "查询结果"}},
                {"type": "paragraph", "content": {"text": "卡片正文"}},
            ],
            "meta": {"assistant_name": "测试助手", "skill_name": "QuerySkill"},
        },
    }

    payload = _build_send_payload(reply, card_enabled=True)

    assert payload["msg_type"] == "text"
    assert payload["content"]["text"] == "这是一个较长的查询结果回复，用于确认仍然会使用卡片进行展示，避免完全退化为纯文本。"


def test_build_send_payload_prefers_template_card_when_selected() -> None:
    reply = {
        "type": "text",
        "text": "fallback text",
        "outbound": {
            "text_fallback": "outbound text",
            "blocks": [{"type": "paragraph", "content": {"text": "旧正文"}}],
            "card_template": {
                "template_id": "query.list",
                "version": "v2",
                "params": {
                    "title": "案件查询结果",
                    "total": 1,
                    "records": [{"fields_text": {"案号": "(2026)粤0101民初100号"}}],
                    "actions": {
                        "next_page": {"callback_action": "query_list_next_page"},
                        "today_hearing": {"callback_action": "query_list_today_hearing"},
                        "week_hearing": {"callback_action": "query_list_week_hearing"},
                    },
                },
            },
            "meta": {"assistant_name": "测试助手", "skill_name": "QuerySkill"},
        },
    }

    payload = _build_send_payload(reply, card_enabled=True)

    assert payload == {
        "msg_type": "text",
        "content": {"text": "outbound text"},
    }


def test_build_send_payload_keeps_minimal_confirm_card() -> None:
    reply = {
        "type": "text",
        "text": "fallback text",
        "outbound": {
            "text_fallback": "请确认",
            "card_template": {
                "template_id": "action.confirm",
                "version": "v1",
                "params": {
                    "message": "请确认是否继续",
                    "action": "create_record",
                    "payload": {"fields": {"案号": "A-1"}},
                },
            },
            "meta": {"assistant_name": "测试助手", "skill_name": "CreateSkill"},
        },
    }

    payload = _build_send_payload(reply, card_enabled=True)

    assert payload["msg_type"] == "interactive"


def test_build_send_payload_uses_text_for_error_reply_even_with_template() -> None:
    reply = {
        "type": "text",
        "text": "fallback text",
        "outbound": {
            "text_fallback": "抱歉，查询失败，请稍后重试。",
            "blocks": [{"type": "paragraph", "content": {"text": "旧正文"}}],
            "card_template": {
                "template_id": "error.notice",
                "version": "v1",
                "params": {"message": "模板正文", "title": "错误"},
            },
            "meta": {"assistant_name": "测试助手", "skill_name": "QuerySkill"},
        },
    }

    payload = _build_send_payload(reply, card_enabled=True)

    assert payload == {
        "msg_type": "text",
        "content": {"text": "抱歉，查询失败，请稍后重试。"},
    }


def test_build_send_payload_falls_back_to_text_when_template_invalid() -> None:
    reply = {
        "type": "text",
        "text": "fallback text",
        "outbound": {
            "text_fallback": "outbound text",
            "blocks": [{"type": "paragraph", "content": {"text": "旧正文"}}],
            "card_template": {
                "template_id": "query.detail",
                "version": "v1",
                "params": {},
            },
            "meta": {"assistant_name": "测试助手", "skill_name": "QuerySkill"},
        },
    }

    payload = _build_send_payload(reply, card_enabled=True)

    assert payload == {
        "msg_type": "text",
        "content": {"text": "outbound text"},
    }
