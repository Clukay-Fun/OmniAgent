from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.l0.engine import L0RuleEngine
from src.core.state import ConversationStateManager, MemoryStateStore


def _build_engine() -> L0RuleEngine:
    state_manager = ConversationStateManager(store=MemoryStateStore())
    return L0RuleEngine(state_manager=state_manager, l0_rules={}, skills_config={})


def test_group_users_do_not_share_last_result_for_ordinal_resolution() -> None:
    state_manager = ConversationStateManager(store=MemoryStateStore())
    engine = L0RuleEngine(state_manager=state_manager, l0_rules={}, skills_config={})

    user_a = "group:oc_g1:user:ou_A"
    user_b = "group:oc_g1:user:ou_B"

    state_manager.set_last_result(user_a, records=[{"record_id": "rec_A_1"}], query_summary="A")
    state_manager.set_last_result(user_b, records=[{"record_id": "rec_B_1"}], query_summary="B")

    decision_a = engine.evaluate(user_a, "删除第一个")
    decision_b = engine.evaluate(user_b, "删除第一个")

    assert decision_a.force_last_result is not None
    assert decision_b.force_last_result is not None
    assert decision_a.force_last_result["records"][0]["record_id"] == "rec_A_1"
    assert decision_b.force_last_result["records"][0]["record_id"] == "rec_B_1"


def test_p2p_scope_remains_plain_user_id() -> None:
    engine = _build_engine()

    decision = engine.evaluate("ou_plain_user", "删除第一个")

    assert decision.handled is True
    assert "请先执行查询" in (decision.reply or {}).get("text", "")
