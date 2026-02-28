from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.runtime.state import ConversationStateManager, MemoryStateStore


def test_state_manager_group_user_isolation_unchanged() -> None:
    manager = ConversationStateManager(store=MemoryStateStore())
    a_session = "feishu:group:oc_g1:user:ou_a"
    b_session = "feishu:group:oc_g1:user:ou_b"

    manager.set_last_result(a_session, records=[{"record_id": "rec_a"}], query_summary="A")
    manager.set_last_result(b_session, records=[{"record_id": "rec_b"}], query_summary="B")

    a_payload = manager.get_last_result_payload(a_session)
    b_payload = manager.get_last_result_payload(b_session)

    assert a_payload is not None
    assert b_payload is not None
    assert a_payload["record_ids"] == ["rec_a"]
    assert b_payload["record_ids"] == ["rec_b"]


def test_state_manager_session_key_alias_methods() -> None:
    manager = ConversationStateManager(store=MemoryStateStore())
    session_key = "feishu:group:oc_g1:user:ou_alias"

    manager.set_last_skill(session_key, "QuerySkill")
    state = manager.get_state_by_session_key(session_key)

    assert state.session_key == session_key
    assert manager.get_last_skill(session_key) == "QuerySkill"

    manager.clear_session(session_key)
    assert manager.get_last_skill(session_key) is None
