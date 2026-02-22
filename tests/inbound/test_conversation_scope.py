from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.api.conversation_scope import build_conversation_user_id


def test_group_chat_scope_includes_group_and_user() -> None:
    scoped = build_conversation_user_id("ou_a", chat_id="oc_g1", chat_type="group")

    assert scoped == "group:oc_g1:user:ou_a"


def test_p2p_scope_keeps_plain_user_id() -> None:
    scoped = build_conversation_user_id("ou_a", chat_id="oc_p2p", chat_type="p2p")

    assert scoped == "ou_a"
