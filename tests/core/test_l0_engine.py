from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.l0.engine import L0RuleEngine  # noqa: E402


class _DummyState:
    def cleanup_expired(self) -> None:
        return

    def get_pending_delete(self, _user_id: str):
        return None

    def get_pending_action(self, _user_id: str):
        return None

    def clear_pending_action(self, _user_id: str) -> None:
        return


def test_l0_update_collect_fields_continues_for_case_no_update_phrase() -> None:
    engine = L0RuleEngine(state_manager=_DummyState(), l0_rules={}, skills_config={})

    should_continue = engine._should_continue_pending_action("修改案号为（2026）粤06民终28498号", "update_collect_fields")

    assert should_continue is True


def test_l0_update_collect_fields_can_clear_for_unrelated_query_phrase() -> None:
    engine = L0RuleEngine(state_manager=_DummyState(), l0_rules={}, skills_config={})

    should_continue = engine._should_continue_pending_action("查一下张三的案子", "update_collect_fields")

    assert should_continue is False
