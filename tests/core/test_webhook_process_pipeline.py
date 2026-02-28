import asyncio
import json
from pathlib import Path
import sys
import types
from types import SimpleNamespace

import pytest


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
from src.core.batch_progress import BatchProgressEvent, BatchProgressPhase


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
        async def ingest(self, **_kwargs):
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
    assert send_payload["msg_type"] == "text"
    content = send_payload["content"]
    assert isinstance(content, dict)
    assert str(content.get("text") or "") == "渲染后的回退文本"


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
        async def ingest(self, **_kwargs):
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
        async def ingest(self, **_kwargs):
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
    assert sent_calls[0]["msg_type"] == "text"
    content = sent_calls[0]["content"]
    assert isinstance(content, dict)
    assert "未开启解析能力" in str(content.get("text") or "")


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
        async def ingest(self, **_kwargs):
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
        async def ingest(self, **_kwargs):
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
        async def handle_card_action_callback(self, user_id, callback_action, callback_value=None):
            assert "ou_1" in user_id
            assert callback_action == "delete_record_confirm"
            assert isinstance(callback_value, dict)
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
        async def handle_card_action_callback(self, user_id, callback_action, callback_value=None):
            assert user_id == "feishu:group:oc_group_1:user:ou_group_u1"
            assert callback_action == "delete_record_confirm"
            assert isinstance(callback_value, dict)
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
        async def handle_card_action_callback(self, user_id, callback_action, callback_value=None):
            assert "ou_2" in user_id
            assert callback_action == "update_record_confirm"
            assert isinstance(callback_value, dict)
            return {"status": "expired", "text": "已过期"}

    settings = SimpleNamespace(
        feishu=SimpleNamespace(encrypt_key=None, verification_token=""),
        webhook=SimpleNamespace(dedup=SimpleNamespace(enabled=False, ttl_seconds=300, max_size=1000)),
    )

    monkeypatch.setattr(webhook_module, "_get_settings", lambda: settings)
    monkeypatch.setattr(webhook_module, "_get_agent_core", lambda: _FakeCore())

    result = asyncio.run(webhook_module.feishu_webhook(_FakeRequest()))

    assert result["status"] == "ok"
    assert any(token in str(result["reason"]) for token in ["过期", "超时"])


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
        async def handle_card_action_callback(self, user_id, callback_action, callback_value=None):
            _ = user_id, callback_action, callback_value
            raise RuntimeError("boom")

    settings = SimpleNamespace(
        feishu=SimpleNamespace(encrypt_key=None, verification_token=""),
        webhook=SimpleNamespace(dedup=SimpleNamespace(enabled=False, ttl_seconds=300, max_size=1000)),
    )

    monkeypatch.setattr(webhook_module, "_get_settings", lambda: settings)
    monkeypatch.setattr(webhook_module, "_get_agent_core", lambda: _FakeCore())

    result = asyncio.run(webhook_module.feishu_webhook(_FakeRequest()))

    assert result["status"] == "ok"
    assert any(token in str(result["reason"]) for token in ["过期", "超时"])


def test_webhook_batch_progress_emitter_sends_start_message_only(monkeypatch) -> None:
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
        return {"message_id": "omni-msg-progress"}

    monkeypatch.setattr(webhook_module, "send_message", _fake_send_message)
    monkeypatch.setattr(webhook_module, "_get_settings", lambda: SimpleNamespace())

    emitter = webhook_module._build_batch_progress_emitter({"chat_id": "oc_progress", "message_id": "om_1"})
    assert emitter is not None

    asyncio.run(
        emitter(
            BatchProgressEvent(
                phase=BatchProgressPhase.START,
                user_id="u1",
                total=3,
            )
        )
    )
    asyncio.run(
        emitter(
            BatchProgressEvent(
                phase=BatchProgressPhase.COMPLETE,
                user_id="u1",
                total=3,
                succeeded=3,
                failed=0,
            )
        )
    )

    assert len(sent_calls) == 1
    assert sent_calls[0]["chat_id"] == "oc_progress"
    assert sent_calls[0]["reply_message_id"] == "om_1"
    content = sent_calls[0]["content"] if isinstance(sent_calls[0]["content"], dict) else {}
    assert "正在执行 3 条操作" in str(content.get("text") or "")


# ── S4: callback dedup ──────────────────────────────────────────────

import time as _time

from src.api.callback_deduper import CallbackDeduper  # noqa: E402


def test_callback_deduper_detects_duplicate() -> None:
    deduper = CallbackDeduper(window_seconds=60)
    key = deduper.build_key(user_id="u1", action="create_record_confirm", payload={"a": 1})
    assert deduper.try_acquire(key) is True
    assert deduper.try_acquire(key) is False
    assert deduper.is_duplicate(key) is True


def test_callback_deduper_expires_after_window() -> None:
    deduper = CallbackDeduper(window_seconds=1)
    key = deduper.build_key(user_id="u1", action="x")
    deduper.mark(key)
    # Artificially expire
    deduper._cache[key] = _time.time() - 2
    assert deduper.is_duplicate(key) is False


def test_callback_deduper_key_is_deterministic() -> None:
    deduper = CallbackDeduper()
    k1 = deduper.build_key(user_id="u1", action="a", payload={"x": 1, "y": 2})
    k2 = deduper.build_key(user_id="u1", action="a", payload={"y": 2, "x": 1})
    assert k1 == k2


# ── S1 修复验证：triplet 校验为必经路径 ──────────────────────────────

from src.core.skills.action_execution_service import ActionExecutionService


def _make_action_service() -> ActionExecutionService:
    """构造一个最小 ActionExecutionService（使用 mock data_writer）。"""
    from unittest.mock import AsyncMock, MagicMock
    dw = MagicMock()
    dw.create = AsyncMock()
    dw.update = AsyncMock()
    dw.delete = AsyncMock()
    return ActionExecutionService(data_writer=dw, linker=MagicMock())


def test_s1_execute_create_rejects_missing_table_id() -> None:
    svc = _make_action_service()
    outcome = asyncio.run(
        svc.execute_create(
            table_id=None,
            table_name="案件",
            fields={"案号": "A-1"},
            idempotency_key=None,
            app_token="app_test",
        )
    )
    assert outcome.success is False
    assert "table_id" in outcome.message


def test_s1_execute_update_rejects_missing_table_id() -> None:
    svc = _make_action_service()
    outcome = asyncio.run(svc.execute_update(
        action_name="update_record", table_id="", table_name="案件",
        record_id="rec_001", fields={"案件状态": "已结案"},
        source_fields={}, idempotency_key=None,
        app_token="app_test",
    ))
    assert outcome.success is False
    assert "table_id" in outcome.message


def test_s1_execute_delete_rejects_missing_record_id() -> None:
    svc = _make_action_service()
    outcome = asyncio.run(svc.execute_delete(
        table_id="tbl_001", table_name="案件",
        record_id="", case_no="A-1", idempotency_key=None,
        app_token="app_test",
    ))
    assert outcome.success is False
    assert "record_id" in outcome.message


# ── S2 修复验证：manager 集成状态机 ──────────────────────────────────

from src.core.state.manager import ConversationStateManager
from src.core.state.memory_store import MemoryStateStore
from src.core.state.models import OperationExecutionStatus, PendingActionStatus


def test_s2_manager_confirm_pending_action() -> None:
    store = MemoryStateStore()
    mgr = ConversationStateManager(store=store)
    mgr.set_pending_action("u1", action="create_record")
    pa = mgr.confirm_pending_action("u1")
    assert pa is not None
    assert pa.status == PendingActionStatus.EXECUTED


def test_s2_manager_cancel_pending_action() -> None:
    store = MemoryStateStore()
    mgr = ConversationStateManager(store=store)
    mgr.set_pending_action("u1", action="delete_record")
    pa = mgr.cancel_pending_action("u1")
    assert pa is not None
    assert pa.status == PendingActionStatus.INVALIDATED


def test_s2_manager_expired_pending_action_cleared() -> None:
    store = MemoryStateStore()
    mgr = ConversationStateManager(store=store, pending_action_ttl_seconds=1)
    mgr.set_pending_action("u1", action="update_record")
    # 手动设置过期
    state = store.get("u1")
    state.pending_action.expires_at = _time.time() - 5
    store.set("u1", state)
    # get_state 会触发过期清理
    refreshed = mgr.get_state("u1")
    assert refreshed.pending_action is None
    history = refreshed.extras.get("pending_action_history")
    assert isinstance(history, list)
    assert history[-1]["status"] == "invalidated"


def test_s2_manager_pending_action_supports_batch_operations() -> None:
    store = MemoryStateStore()
    mgr = ConversationStateManager(store=store)
    operations = [
        {"record_id": "rec_1", "fields": {"案件状态": "进行中"}},
        {"record_id": "rec_2", "fields": {"案件状态": "已结案"}},
    ]

    mgr.set_pending_action(
        "u_batch",
        action="batch_update_records",
        payload={"table_id": "tbl_main"},
        operations=operations,
    )

    pending = mgr.get_pending_action("u_batch")

    assert pending is not None
    assert pending.action == "batch_update_records"
    assert [item.payload for item in pending.operations] == operations
    assert all(item.status == OperationExecutionStatus.PENDING for item in pending.operations)


# ── S4 修复验证：callback_deduper 已接入 webhook ──────────────────────

def test_s4_callback_deduper_is_imported_in_webhook() -> None:
    """验证 CallbackDeduper 已在 webhook.py 中被导入和使用。"""
    import importlib
    webhook_mod = importlib.import_module("src.api.webhook")
    assert hasattr(webhook_mod, "CallbackDeduper")
    assert hasattr(webhook_mod, "_get_callback_deduper")


def test_s4_callback_deduper_window_uses_600s(monkeypatch) -> None:
    monkeypatch.setattr(webhook_module, "_callback_deduper", None)
    deduper = webhook_module._get_callback_deduper()
    assert deduper._window == 600


def test_s4_webhook_semantic_dedup_short_circuit(monkeypatch, caplog) -> None:
    class _FakeRequest:
        def __init__(self, payload: dict[str, object]):
            self._payload = payload

        async def json(self):
            return self._payload

    calls = {"count": 0}

    class _FakeCore:
        async def handle_card_action_callback(self, user_id, callback_action, callback_value=None):
            _ = user_id, callback_action, callback_value
            calls["count"] += 1
            return {"status": "processed", "text": "已处理"}

    settings = SimpleNamespace(
        feishu=SimpleNamespace(encrypt_key=None, verification_token=""),
        webhook=SimpleNamespace(dedup=SimpleNamespace(enabled=True, ttl_seconds=300, max_size=1000)),
    )

    payload_1 = {
        "header": {"event_id": "evt_cb_sem_1"},
        "event": {
            "operator": {"operator_id": {"open_id": "ou_sem_u1"}},
            "open_chat_id": "oc_sem_1",
            "action": {
                "value": {
                    "callback_action": "delete_record_confirm",
                    "record_id": "rec_001",
                }
            },
        },
    }
    payload_2 = {
        "header": {"event_id": "evt_cb_sem_2"},
        "event": {
            "operator": {"operator_id": {"open_id": "ou_sem_u1"}},
            "open_chat_id": "oc_sem_1",
            "action": {
                "value": {
                    "callback_action": "delete_record_confirm",
                    "record_id": "rec_001",
                }
            },
        },
    }

    monkeypatch.setattr(webhook_module, "_get_settings", lambda: settings)
    monkeypatch.setattr(webhook_module, "_get_agent_core", lambda: _FakeCore())
    monkeypatch.setattr(webhook_module, "_deduplicator", None)
    monkeypatch.setattr(webhook_module, "_callback_deduper", None)

    with caplog.at_level("INFO"):
        first = asyncio.run(webhook_module.feishu_webhook(_FakeRequest(payload_1)))
        second = asyncio.run(webhook_module.feishu_webhook(_FakeRequest(payload_2)))

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert "已处理" in second["reason"]
    assert calls["count"] == 1
    assert any(getattr(record, "event_code", "") == "callback.duplicated" for record in caplog.records)


def test_s4_webhook_semantic_dedup_blocks_concurrent_callbacks(monkeypatch) -> None:
    class _FakeRequest:
        def __init__(self, payload: dict[str, object]):
            self._payload = payload

        async def json(self):
            return self._payload

    calls = {"count": 0}

    class _FakeCore:
        async def handle_card_action_callback(self, user_id, callback_action, callback_value=None):
            _ = user_id, callback_action, callback_value
            calls["count"] += 1
            await asyncio.sleep(0.02)
            return {"status": "processed", "text": "已处理"}

    settings = SimpleNamespace(
        feishu=SimpleNamespace(encrypt_key=None, verification_token=""),
        webhook=SimpleNamespace(dedup=SimpleNamespace(enabled=True, ttl_seconds=300, max_size=1000)),
    )

    payload_1 = {
        "header": {"event_id": "evt_cb_concurrent_1"},
        "event": {
            "operator": {"operator_id": {"open_id": "ou_concurrent_u1"}},
            "open_chat_id": "oc_concurrent_1",
            "action": {
                "value": {
                    "callback_action": "delete_record_confirm",
                    "record_id": "rec_001",
                }
            },
        },
    }
    payload_2 = {
        "header": {"event_id": "evt_cb_concurrent_2"},
        "event": {
            "operator": {"operator_id": {"open_id": "ou_concurrent_u1"}},
            "open_chat_id": "oc_concurrent_1",
            "action": {
                "value": {
                    "callback_action": "delete_record_confirm",
                    "record_id": "rec_001",
                }
            },
        },
    }

    monkeypatch.setattr(webhook_module, "_get_settings", lambda: settings)
    monkeypatch.setattr(webhook_module, "_get_agent_core", lambda: _FakeCore())
    monkeypatch.setattr(webhook_module, "_deduplicator", None)
    monkeypatch.setattr(webhook_module, "_callback_deduper", None)

    async def _invoke_both() -> tuple[dict[str, object], dict[str, object]]:
        return await asyncio.gather(
            webhook_module.feishu_webhook(_FakeRequest(payload_1)),
            webhook_module.feishu_webhook(_FakeRequest(payload_2)),
        )

    first, second = asyncio.run(_invoke_both())

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert calls["count"] == 1
