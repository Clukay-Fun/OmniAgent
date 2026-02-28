from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.config import Settings
from src.core.runtime.state.factory import create_state_store
from src.core.runtime.state.memory_store import MemoryStateStore


def test_state_store_factory_defaults_to_memory() -> None:
    settings = Settings()

    store = create_state_store(settings)

    assert isinstance(store, MemoryStateStore)


def test_state_store_factory_falls_back_when_redis_init_fails(monkeypatch) -> None:
    settings = Settings.model_validate({"state_store": {"backend": "redis"}})

    def _boom(_redis_settings):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr("src.core.runtime.state.factory.RedisStateStore.from_settings", _boom)

    store = create_state_store(settings)

    assert isinstance(store, MemoryStateStore)


def test_state_store_factory_uses_redis_when_available(monkeypatch) -> None:
    settings = Settings.model_validate({"state_store": {"backend": "redis"}})
    sentinel = object()

    monkeypatch.setattr(
        "src.core.runtime.state.factory.RedisStateStore.from_settings",
        lambda _redis_settings: sentinel,
    )

    store = create_state_store(settings)

    assert store is sentinel


def test_state_store_factory_unknown_backend_falls_back_to_memory() -> None:
    settings = Settings.model_validate({"state_store": {"backend": "not-supported"}})

    store = create_state_store(settings)

    assert isinstance(store, MemoryStateStore)
