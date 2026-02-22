from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.orchestrator import AgentOrchestrator  # noqa: E402


class _FakeStateManager:
    def __init__(self) -> None:
        self.pending = SimpleNamespace(action="delete_record", payload={"record_id": "rec_1"})

    def get_pending_action(self, _user_id: str):
        return self.pending


class _FakeRouter:
    def get_skill(self, _name: str):
        raise AssertionError("skill should not be invoked on callback mismatch")


def test_callback_action_mismatch_returns_expired() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._state_manager = _FakeStateManager()
    orchestrator._router = _FakeRouter()

    result = asyncio.run(orchestrator.handle_card_action_callback("u1", "update_record_confirm"))

    assert result["status"] == "expired"
    assert "过期" in result["text"]
