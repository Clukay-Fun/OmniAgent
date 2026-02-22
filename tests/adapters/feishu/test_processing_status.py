from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.feishu.processing_status import (  # noqa: E402
    FeishuReactionStatusEmitter,
    create_reaction_status_emitter,
)
from src.core.processing_status import ProcessingStatus, ProcessingStatusEvent  # noqa: E402


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
        "src.adapters.channels.feishu.processing_status.set_message_reaction",
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
