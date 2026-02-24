from __future__ import annotations

from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.state.models import ConversationState, PendingDeleteState
from src.core.state.redis_store import RedisStateStore


class _FakeRedis:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._data[key] = value

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def scan_iter(self, match: str | None = None):
        if not match or not match.endswith("*"):
            for key in list(self._data.keys()):
                yield key
            return
        prefix = match[:-1]
        for key in list(self._data.keys()):
            if key.startswith(prefix):
                yield key


def _build_state(session_key: str, expires_at: float) -> ConversationState:
    now = time.time()
    return ConversationState(
        user_id=session_key,
        created_at=now,
        updated_at=now,
        expires_at=expires_at,
        pending_delete=PendingDeleteState(
            record_id="rec_1",
            record_summary="案件A",
            table_id="tbl_1",
            created_at=now,
            expires_at=expires_at,
        ),
    )


def test_redis_state_store_roundtrip_with_nested_fields() -> None:
    store = RedisStateStore(client=_FakeRedis(), key_prefix="test:state:")
    session_key = "group:oc_g1:user:ou_u1"
    state = _build_state(session_key=session_key, expires_at=time.time() + 120)

    store.set(session_key, state)
    loaded = store.get(session_key)

    assert loaded is not None
    assert loaded.session_key == session_key
    assert loaded.pending_delete is not None
    assert loaded.pending_delete.record_id == "rec_1"


def test_redis_state_store_supports_legacy_user_id_keywords() -> None:
    store = RedisStateStore(client=_FakeRedis(), key_prefix="test:state:")
    state = _build_state(session_key="ou_u1", expires_at=time.time() + 60)

    store.set(user_id="ou_u1", state=state)
    loaded = store.get(user_id="ou_u1")

    assert loaded is not None
    assert loaded.user_id == "ou_u1"


def test_redis_state_store_list_and_cleanup_expired() -> None:
    store = RedisStateStore(client=_FakeRedis(), key_prefix="test:state:")
    live = _build_state(session_key="ou_live", expires_at=time.time() + 300)
    expired = _build_state(session_key="ou_expired", expires_at=time.time() - 1)

    store.set("ou_live", live)
    store.set("ou_expired", expired)
    assert set(store.list_session_keys()) == {"ou_live", "ou_expired"}

    store.cleanup_expired()

    assert store.get("ou_expired") is None
    assert store.get("ou_live") is not None
    assert store.active_count() == 1
