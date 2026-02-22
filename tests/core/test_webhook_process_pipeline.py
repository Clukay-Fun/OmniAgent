import asyncio
import json
from pathlib import Path
import sys
import types
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

# webhook imports optional runtime dependencies at import time.
sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))
crypto_module = types.ModuleType("Crypto")
crypto_cipher_module = types.ModuleType("Crypto.Cipher")
setattr(crypto_cipher_module, "AES", object())
setattr(crypto_module, "Cipher", crypto_cipher_module)
sys.modules.setdefault("Crypto", crypto_module)
sys.modules.setdefault("Crypto.Cipher", crypto_cipher_module)

import src.api.webhook as webhook_module


def test_process_message_sends_formatter_payload(monkeypatch) -> None:
    sent_calls: list[dict[str, object]] = []

    async def _fake_send_message(settings, chat_id, msg_type, content, reply_message_id=None):
        sent_calls.append(
            {
                "settings": settings,
                "chat_id": chat_id,
                "msg_type": msg_type,
                "content": content,
                "reply_message_id": reply_message_id,
            }
        )
        return {"message_id": "omni-msg-1"}

    class _FakeAgentCore:
        async def handle_message(self, user_id, text, **kwargs):
            assert user_id == "ou_test_user"
            assert text == "请帮我看一下案件进展"
            assert kwargs["chat_id"] == "oc_pipeline"
            return {
                "type": "text",
                "text": "fallback",
                "outbound": {
                    "text_fallback": "渲染后的回退文本",
                    "blocks": [
                        {
                            "type": "paragraph",
                            "content": {"text": "来自 outbound 的正文"},
                        }
                    ],
                    "meta": {"assistant_name": "测试助手", "skill_name": "QuerySkill"},
                },
            }

    class _FakeUserManager:
        async def get_or_create_profile(self, **_kwargs):
            return SimpleNamespace(name="张三")

    settings = SimpleNamespace(reply=SimpleNamespace(card_enabled=True))

    monkeypatch.setattr(webhook_module, "_get_settings", lambda: settings)
    monkeypatch.setattr(webhook_module, "_get_agent_core", lambda: _FakeAgentCore())
    monkeypatch.setattr(webhook_module, "_get_user_manager", lambda: _FakeUserManager())
    monkeypatch.setattr(webhook_module, "send_message", _fake_send_message)

    class _AlwaysProcessAssembler:
        def ingest(self, **_kwargs):
            return SimpleNamespace(should_process=True, text="请帮我看一下案件进展", reason="fast_path")

    monkeypatch.setattr(webhook_module, "_get_chunk_assembler", lambda: _AlwaysProcessAssembler())

    message = {
        "chat_id": "oc_pipeline",
        "chat_type": "p2p",
        "message_id": "msg_pipeline",
        "content": json.dumps({"text": "请帮我看一下案件进展"}, ensure_ascii=False),
    }
    sender = {"sender_id": {"open_id": "ou_test_user"}}

    ok = asyncio.run(webhook_module._process_message(message, sender))

    assert ok is True
    assert len(sent_calls) == 1
    send_payload = sent_calls[0]
    assert send_payload["chat_id"] == "oc_pipeline"
    assert send_payload["msg_type"] == "interactive"
    content = send_payload["content"]
    assert isinstance(content, dict)
    assert content["elements"][0]["tag"] == "markdown"
    assert "来自 outbound 的正文" in content["elements"][0]["content"]


def test_process_message_uses_group_user_scoped_key(monkeypatch) -> None:
    sent_calls: list[dict[str, object]] = []

    async def _fake_send_message(settings, chat_id, msg_type, content, reply_message_id=None):
        sent_calls.append(
            {
                "settings": settings,
                "chat_id": chat_id,
                "msg_type": msg_type,
                "content": content,
                "reply_message_id": reply_message_id,
            }
        )
        return {"message_id": "omni-msg-2"}

    class _FakeAgentCore:
        async def handle_message(self, user_id, text, **kwargs):
            assert user_id == "group:oc_group_1:user:ou_test_user"
            assert text == "删除第一个。"
            assert kwargs["chat_id"] == "oc_group_1"
            return {"type": "text", "text": "ok"}

    class _FakeUserManager:
        async def get_or_create_profile(self, **_kwargs):
            return SimpleNamespace(name="张三")

    settings = SimpleNamespace(reply=SimpleNamespace(card_enabled=True))

    monkeypatch.setattr(webhook_module, "_get_settings", lambda: settings)
    monkeypatch.setattr(webhook_module, "_get_agent_core", lambda: _FakeAgentCore())
    monkeypatch.setattr(webhook_module, "_get_user_manager", lambda: _FakeUserManager())
    monkeypatch.setattr(webhook_module, "send_message", _fake_send_message)

    class _AlwaysProcessAssembler:
        def ingest(self, **_kwargs):
            return SimpleNamespace(should_process=True, text="删除第一个。", reason="fast_path")

    monkeypatch.setattr(webhook_module, "_get_chunk_assembler", lambda: _AlwaysProcessAssembler())

    message = {
        "chat_id": "oc_group_1",
        "chat_type": "group",
        "message_id": "msg_group_1",
        "content": json.dumps({"text": "删除第一个。"}, ensure_ascii=False),
    }
    sender = {"sender_id": {"open_id": "ou_test_user"}}

    ok = asyncio.run(webhook_module._process_message(message, sender))

    assert ok is True
    assert len(sent_calls) == 1


def test_process_file_message_falls_back_to_guidance_when_unavailable(monkeypatch) -> None:
    sent_calls: list[dict[str, object]] = []
    agent_called = {"value": False}

    async def _fake_send_message(settings, chat_id, msg_type, content, reply_message_id=None):
        sent_calls.append(
            {
                "settings": settings,
                "chat_id": chat_id,
                "msg_type": msg_type,
                "content": content,
                "reply_message_id": reply_message_id,
            }
        )
        return {"message_id": "omni-msg-file-1"}

    class _FakeAgentCore:
        async def handle_message(self, user_id, text, **kwargs):
            del user_id, text, kwargs
            agent_called["value"] = True
            return {"type": "text", "text": "should-not-be-used"}

    class _FakeUserManager:
        async def get_or_create_profile(self, **_kwargs):
            return SimpleNamespace(name="张三")

    settings = SimpleNamespace(
        reply=SimpleNamespace(card_enabled=True),
        file_pipeline=SimpleNamespace(enabled=True, max_bytes=1024, timeout_seconds=3),
        file_extractor=SimpleNamespace(enabled=False, provider="none", api_key="", api_base="", fail_open=True),
    )

    monkeypatch.setattr(webhook_module, "_get_settings", lambda: settings)
    monkeypatch.setattr(webhook_module, "_get_agent_core", lambda: _FakeAgentCore())
    monkeypatch.setattr(webhook_module, "_get_user_manager", lambda: _FakeUserManager())
    monkeypatch.setattr(webhook_module, "send_message", _fake_send_message)

    class _AlwaysProcessAssembler:
        def ingest(self, **_kwargs):
            return SimpleNamespace(should_process=True, text="[收到文件消息]", reason="fast_path")

    monkeypatch.setattr(webhook_module, "_get_chunk_assembler", lambda: _AlwaysProcessAssembler())

    async def _fake_resolve_file_markdown(*_args, **_kwargs):
        return "", "已收到文件，但当前未开启解析能力，请稍后再试或补充文字说明。"

    monkeypatch.setattr(webhook_module, "resolve_file_markdown", _fake_resolve_file_markdown)

    message = {
        "chat_id": "oc_pipeline",
        "chat_type": "p2p",
        "message_id": "msg_file_1",
        "message_type": "file",
        "content": json.dumps({"file_key": "f1", "file_name": "合同.pdf"}, ensure_ascii=False),
    }
    sender = {"sender_id": {"open_id": "ou_test_user"}}

    ok = asyncio.run(webhook_module._process_message(message, sender))

    assert ok is True
    assert agent_called["value"] is False
    assert len(sent_calls) == 1
    content = sent_calls[0]["content"]
    assert isinstance(content, dict)
    payload = json.dumps(content, ensure_ascii=False)
    assert "未开启解析能力" in payload
