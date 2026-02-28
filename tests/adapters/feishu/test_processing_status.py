from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.feishu.actions.processing_status import (  # noqa: E402
    FeishuReactionStatusEmitter,
    create_reaction_status_emitter,
)
from src.core.foundation.progress.processing_status import ProcessingStatus, ProcessingStatusEvent  # noqa: E402


def _settings(reaction_enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(reply=SimpleNamespace(reaction_enabled=reaction_enabled))


def test_create_reaction_status_emitter_respects_flag() -> None:
    assert create_reaction_status_emitter(_settings(False), "msg_1") is None
    assert create_reaction_status_emitter(_settings(True), "") is None
    assert create_reaction_status_emitter(_settings(True), "msg_1") is not None


def test_feishu_reaction_status_emitter_fail_open(monkeypatch) -> None:
    async def _raise(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "src.adapters.channels.feishu.actions.processing_status.set_message_reaction",
        _raise,
    )

    emitter = FeishuReactionStatusEmitter(settings=_settings(True), message_id="msg_1")
    asyncio.run(
        emitter(
            ProcessingStatusEvent(
                status=ProcessingStatus.THINKING,
                user_id="u1",
                chat_id="oc_1",
            )
        )
    )


def test_feishu_reaction_status_emitter_disables_on_invalid_reaction_type(monkeypatch, caplog) -> None:
    calls = {"count": 0}

    async def _raise(*_args, **_kwargs):
        calls["count"] += 1
        raise RuntimeError("reaction type is invalid")

    monkeypatch.setattr(
        "src.adapters.channels.feishu.actions.processing_status.set_message_reaction",
        _raise,
    )

    emitter = FeishuReactionStatusEmitter(settings=_settings(True), message_id="msg_1")
    event = ProcessingStatusEvent(
        status=ProcessingStatus.THINKING,
        user_id="u1",
        chat_id="oc_1",
    )

    with caplog.at_level("INFO"):
        asyncio.run(emitter(event))
        asyncio.run(emitter(event))

    assert calls["count"] == 1
    assert "已禁用该状态的 processing status reaction" in caplog.text


def test_feishu_reaction_status_emitter_uses_single_reaction_per_status(monkeypatch) -> None:
    calls: list[str] = []

    async def _send(*_args, **kwargs):
        reaction_type = kwargs.get("reaction_type")
        calls.append(str(reaction_type))
        return None

    monkeypatch.setattr(
        "src.adapters.channels.feishu.actions.processing_status.set_message_reaction",
        _send,
    )

    emitter = FeishuReactionStatusEmitter(settings=_settings(True), message_id="msg_1")
    event = ProcessingStatusEvent(
        status=ProcessingStatus.THINKING,
        user_id="u1",
        chat_id="oc_1",
    )
    asyncio.run(emitter(event))
    asyncio.run(emitter(event))

    assert calls == ["OK", "OK"]


def test_feishu_reaction_status_emitter_removes_processing_reaction_on_done(monkeypatch) -> None:
    add_calls: list[str] = []
    delete_calls: list[str] = []

    async def _send(*_args, **kwargs):
        reaction_type = kwargs.get("reaction_type")
        add_calls.append(str(reaction_type))
        return "reaction_1"

    async def _delete(*_args, **kwargs):
        delete_calls.append(str(kwargs.get("reaction_id") or ""))

    monkeypatch.setattr(
        "src.adapters.channels.feishu.actions.processing_status.set_message_reaction",
        _send,
    )
    monkeypatch.setattr(
        "src.adapters.channels.feishu.actions.processing_status.delete_message_reaction",
        _delete,
    )

    emitter = FeishuReactionStatusEmitter(settings=_settings(True), message_id="msg_1")
    asyncio.run(
        emitter(
            ProcessingStatusEvent(
                status=ProcessingStatus.THINKING,
                user_id="u1",
                chat_id="oc_1",
            )
        )
    )
    asyncio.run(
        emitter(
            ProcessingStatusEvent(
                status=ProcessingStatus.DONE,
                user_id="u1",
                chat_id="oc_1",
            )
        )
    )

    assert add_calls == ["OK"]
    assert delete_calls == ["reaction_1"]


def test_feishu_reaction_status_emitter_keeps_retrying_non_invalid_errors(monkeypatch) -> None:
    calls = {"count": 0}

    async def _raise(*_args, **_kwargs):
        calls["count"] += 1
        raise RuntimeError("temporary network error")

    monkeypatch.setattr(
        "src.adapters.channels.feishu.actions.processing_status.set_message_reaction",
        _raise,
    )

    emitter = FeishuReactionStatusEmitter(settings=_settings(True), message_id="msg_1")
    event = ProcessingStatusEvent(
        status=ProcessingStatus.THINKING,
        user_id="u1",
        chat_id="oc_1",
    )
    asyncio.run(emitter(event))
    asyncio.run(emitter(event))

    assert calls["count"] == 2
