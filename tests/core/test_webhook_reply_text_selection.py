from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[2]
FEISHU_AGENT_ROOT = ROOT / "agent" / "feishu-agent"
sys.path.insert(0, str(FEISHU_AGENT_ROOT))

# orchestrator imports Postgres client, which requires asyncpg at import time.
sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))
crypto_module = types.ModuleType("Crypto")
crypto_cipher_module = types.ModuleType("Crypto.Cipher")
setattr(crypto_cipher_module, "AES", object())
setattr(crypto_module, "Cipher", crypto_cipher_module)
sys.modules.setdefault("Crypto", crypto_module)
sys.modules.setdefault("Crypto.Cipher", crypto_cipher_module)

from src.api.webhook import _build_send_payload, _pick_reply_text


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

    assert payload["msg_type"] == "interactive"
    assert payload["card"]["elements"][0]["tag"] == "markdown"
    assert "卡片正文" in payload["card"]["elements"][0]["content"]
