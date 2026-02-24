from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_ws_client_registers_bot_presence_events() -> None:
    content = (REPO_ROOT / "apps" / "agent-host" / "src" / "api" / "ws_client.py").read_text(encoding="utf-8")
    assert '"im.chat.access_event.bot_p2p_chat_entered_v1"' in content
    assert '"im.chat.member.bot.added_v1"' in content
