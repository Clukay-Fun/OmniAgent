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
            assert user_id == "feishu:group:oc_group_1:user:ou_test_user"
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
        return "", "已收到文件，但当前未开启解析能力，请稍后再试或补充文字说明。", "none"

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


def test_process_image_message_sends_status_and_ocr_summary(monkeypatch) -> None:
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
        return {"message_id": f"omni-msg-{len(sent_calls)}"}

    class _FakeAgentCore:
        async def handle_message(self, user_id, text, **kwargs):
            assert user_id == "ou_test_user"
            assert text == "[收到图片消息]"
            assert kwargs["file_markdown"].startswith("## 图片识别结果")
            return {"type": "text", "text": "已分析图片内容"}

    class _FakeUserManager:
        async def get_or_create_profile(self, **_kwargs):
            return SimpleNamespace(name="张三")

    settings = SimpleNamespace(
        reply=SimpleNamespace(card_enabled=True),
        file_pipeline=SimpleNamespace(enabled=True, max_bytes=1024, timeout_seconds=3),
        file_extractor=SimpleNamespace(enabled=True, provider="llm", api_key="k", api_base="https://api.example.com", fail_open=True),
    )

    monkeypatch.setattr(webhook_module, "_get_settings", lambda: settings)
    monkeypatch.setattr(webhook_module, "_get_agent_core", lambda: _FakeAgentCore())
    monkeypatch.setattr(webhook_module, "_get_user_manager", lambda: _FakeUserManager())
    monkeypatch.setattr(webhook_module, "send_message", _fake_send_message)

    class _AlwaysProcessAssembler:
        def ingest(self, **_kwargs):
            return SimpleNamespace(should_process=True, text="[收到图片消息]", reason="fast_path")

    monkeypatch.setattr(webhook_module, "_get_chunk_assembler", lambda: _AlwaysProcessAssembler())

    async def _fake_resolve_file_markdown(*_args, **_kwargs):
        return "## 图片识别结果\n\n合同第1条", "", "llm"

    monkeypatch.setattr(webhook_module, "resolve_file_markdown", _fake_resolve_file_markdown)

    message = {
        "chat_id": "oc_pipeline",
        "chat_type": "p2p",
        "message_id": "msg_image_1",
        "message_type": "image",
        "content": json.dumps({"image_key": "img1", "file_type": "png"}, ensure_ascii=False),
    }
    sender = {"sender_id": {"open_id": "ou_test_user"}}

    ok = asyncio.run(webhook_module._process_message(message, sender))

    assert ok is True
    assert len(sent_calls) == 2
    assert sent_calls[0]["msg_type"] == "text"
    first_payload = sent_calls[0]["content"]
    assert isinstance(first_payload, dict)
    assert "正在识别图片内容" in str(first_payload.get("text") or "")
    second_payload = json.dumps(sent_calls[1]["content"], ensure_ascii=False)
    assert "图片识别完成" in second_payload


def test_process_audio_message_transcript_flows_as_text(monkeypatch) -> None:
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
        return {"message_id": f"omni-msg-{len(sent_calls)}"}

    class _FakeAgentCore:
        async def handle_message(self, user_id, text, **kwargs):
            assert user_id == "ou_test_user"
            assert text == "请帮我查一下今天案件"
            assert kwargs["file_markdown"] == ""
            return {"type": "text", "text": "好的，正在查询"}

    class _FakeUserManager:
        async def get_or_create_profile(self, **_kwargs):
            return SimpleNamespace(name="张三")

    settings = SimpleNamespace(
        reply=SimpleNamespace(card_enabled=True),
        file_pipeline=SimpleNamespace(enabled=True, max_bytes=1024, timeout_seconds=3),
        file_extractor=SimpleNamespace(enabled=True, provider="llm", api_key="k", api_base="https://api.example.com", fail_open=True),
    )

    monkeypatch.setattr(webhook_module, "_get_settings", lambda: settings)
    monkeypatch.setattr(webhook_module, "_get_agent_core", lambda: _FakeAgentCore())
    monkeypatch.setattr(webhook_module, "_get_user_manager", lambda: _FakeUserManager())
    monkeypatch.setattr(webhook_module, "send_message", _fake_send_message)

    class _AlwaysProcessAssembler:
        def ingest(self, **_kwargs):
            return SimpleNamespace(should_process=True, text="[收到语音消息]", reason="fast_path")

    monkeypatch.setattr(webhook_module, "_get_chunk_assembler", lambda: _AlwaysProcessAssembler())

    async def _fake_resolve_file_markdown(*_args, **_kwargs):
        return "请帮我查一下今天案件", "", "llm"

    monkeypatch.setattr(webhook_module, "resolve_file_markdown", _fake_resolve_file_markdown)

    message = {
        "chat_id": "oc_pipeline",
        "chat_type": "p2p",
        "message_id": "msg_audio_1",
        "message_type": "audio",
        "content": json.dumps({"audio_key": "aud1", "file_type": "mp3"}, ensure_ascii=False),
    }
    sender = {"sender_id": {"open_id": "ou_test_user"}}

    ok = asyncio.run(webhook_module._process_message(message, sender))

    assert ok is True
    assert len(sent_calls) == 2
    status_payload = sent_calls[0]["content"]
    assert isinstance(status_payload, dict)
    assert "正在识别语音内容" in str(status_payload.get("text") or "")


def test_webhook_card_callback_returns_processed(monkeypatch) -> None:
    class _FakeRequest:
        async def json(self):
            return {
                "header": {"event_id": "evt_cb_1"},
                "event": {
                    "operator": {"operator_id": {"open_id": "ou_1"}},
                    "open_chat_id": "oc_1",
                    "action": {"value": {"callback_action": "delete_record_confirm"}},
                },
            }

    class _FakeCore:
        async def handle_card_action_callback(self, user_id, callback_action):
            assert "ou_1" in user_id
            assert callback_action == "delete_record_confirm"
            return {"status": "processed", "text": "已处理"}

    settings = SimpleNamespace(
        feishu=SimpleNamespace(encrypt_key=None, verification_token=""),
        webhook=SimpleNamespace(dedup=SimpleNamespace(enabled=True, ttl_seconds=300, max_size=1000)),
    )

    monkeypatch.setattr(webhook_module, "_get_settings", lambda: settings)
    monkeypatch.setattr(webhook_module, "_get_agent_core", lambda: _FakeCore())
    monkeypatch.setattr(webhook_module, "_deduplicator", None)

    result = asyncio.run(webhook_module.feishu_webhook(_FakeRequest()))

    assert result["status"] == "ok"
    assert "已处理" in result["reason"]


def test_webhook_card_callback_group_user_isolation_session_key(monkeypatch) -> None:
    class _FakeRequest:
        async def json(self):
            return {
                "header": {"event_id": "evt_cb_group_1"},
                "event": {
                    "operator": {"operator_id": {"open_id": "ou_group_u1"}},
                    "open_chat_id": "oc_group_1",
                    "chat_type": "group",
                    "action": {"value": {"callback_action": "delete_record_confirm"}},
                },
            }

    class _FakeCore:
        async def handle_card_action_callback(self, user_id, callback_action):
            assert user_id == "feishu:group:oc_group_1:user:ou_group_u1"
            assert callback_action == "delete_record_confirm"
            return {"status": "processed", "text": "已处理"}

    settings = SimpleNamespace(
        feishu=SimpleNamespace(encrypt_key=None, verification_token=""),
        webhook=SimpleNamespace(dedup=SimpleNamespace(enabled=False, ttl_seconds=300, max_size=1000)),
    )

    monkeypatch.setattr(webhook_module, "_get_settings", lambda: settings)
    monkeypatch.setattr(webhook_module, "_get_agent_core", lambda: _FakeCore())

    result = asyncio.run(webhook_module.feishu_webhook(_FakeRequest()))

    assert result["status"] == "ok"


def test_webhook_card_callback_returns_expired(monkeypatch) -> None:
    class _FakeRequest:
        async def json(self):
            return {
                "header": {"event_id": "evt_cb_2"},
                "event": {
                    "operator": {"operator_id": {"open_id": "ou_2"}},
                    "open_chat_id": "oc_2",
                    "action": {"value": {"callback_action": "update_record_confirm"}},
                },
            }

    class _FakeCore:
        async def handle_card_action_callback(self, user_id, callback_action):
            assert "ou_2" in user_id
            assert callback_action == "update_record_confirm"
            return {"status": "expired", "text": "已过期"}

    settings = SimpleNamespace(
        feishu=SimpleNamespace(encrypt_key=None, verification_token=""),
        webhook=SimpleNamespace(dedup=SimpleNamespace(enabled=False, ttl_seconds=300, max_size=1000)),
    )

    monkeypatch.setattr(webhook_module, "_get_settings", lambda: settings)
    monkeypatch.setattr(webhook_module, "_get_agent_core", lambda: _FakeCore())

    result = asyncio.run(webhook_module.feishu_webhook(_FakeRequest()))

    assert result["status"] == "ok"
    assert "过期" in result["reason"]


def test_webhook_card_callback_failure_is_non_blocking(monkeypatch) -> None:
    class _FakeRequest:
        async def json(self):
            return {
                "header": {"event_id": "evt_cb_3"},
                "event": {
                    "operator": {"operator_id": {"open_id": "ou_3"}},
                    "open_chat_id": "oc_3",
                    "action": {"value": {"callback_action": "delete_record_confirm"}},
                },
            }

    class _FakeCore:
        async def handle_card_action_callback(self, user_id, callback_action):
            _ = user_id, callback_action
            raise RuntimeError("boom")

    settings = SimpleNamespace(
        feishu=SimpleNamespace(encrypt_key=None, verification_token=""),
        webhook=SimpleNamespace(dedup=SimpleNamespace(enabled=False, ttl_seconds=300, max_size=1000)),
    )

    monkeypatch.setattr(webhook_module, "_get_settings", lambda: settings)
    monkeypatch.setattr(webhook_module, "_get_agent_core", lambda: _FakeCore())

    result = asyncio.run(webhook_module.feishu_webhook(_FakeRequest()))

    assert result["status"] == "ok"
    assert "过期" in result["reason"]
