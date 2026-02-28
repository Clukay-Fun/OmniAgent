from __future__ import annotations

from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.runtime.state import ConversationStateManager, MemoryStateStore
from src.core.runtime.state.models import ConversationState, MessageChunkState


def test_conversation_state_from_dict_parses_message_chunk() -> None:
    now = time.time()
    state = ConversationState.from_dict(
        {
            "user_id": "u1",
            "created_at": now,
            "updated_at": now,
            "expires_at": now + 60,
            "message_chunk": {
                "segments": ["A", "B"],
                "started_at": now,
                "last_at": now,
            },
        }
    )

    assert state.message_chunk is not None
    assert state.message_chunk.segments == ["A", "B"]


def test_state_manager_set_and_get_message_chunk() -> None:
    manager = ConversationStateManager(store=MemoryStateStore())
    now = time.time()
    chunk = MessageChunkState(segments=["hello"], started_at=now - 1.0, last_at=now)

    manager.set_message_chunk("u1", chunk)
    loaded = manager.get_message_chunk("u1")

    assert loaded is not None
    assert loaded.segments == ["hello"]
    assert loaded.last_at == now


def test_state_manager_get_message_chunk_clears_stale_chunk(monkeypatch) -> None:
    manager = ConversationStateManager(store=MemoryStateStore())
    manager.set_message_chunk(
        "u2",
        MessageChunkState(segments=["A"], started_at=10.0, last_at=10.0),
    )

    monkeypatch.setattr("src.core.runtime.state.manager.time.time", lambda: 20.0)

    loaded = manager.get_message_chunk("u2")

    assert loaded is None
    assert manager.get_state("u2").message_chunk is None
