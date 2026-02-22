from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.config import SessionSettings
from src.core.session import SessionManager


def test_trim_context_to_token_budget_removes_oldest_messages() -> None:
    manager = SessionManager(
        SessionSettings(
            ttl_minutes=30,
            max_rounds=20,
            max_context_tokens=4000,
        )
    )
    user_id = "u-token-budget"

    manager.add_message(user_id, "user", "A" * 40)
    manager.add_message(user_id, "assistant", "B" * 40)
    manager.add_message(user_id, "user", "C" * 40)
    manager.add_message(user_id, "assistant", "D" * 40)

    removed = manager.trim_context_to_token_budget(
        user_id=user_id,
        max_tokens=35,
        keep_recent_messages=2,
    )

    context = manager.get_context(user_id)
    assert removed == 2
    assert [item["content"] for item in context] == ["C" * 40, "D" * 40]
