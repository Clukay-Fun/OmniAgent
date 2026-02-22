from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.config import load_settings


def test_state_store_defaults_to_memory_backend() -> None:
    settings = load_settings(config_path="/path/not/exists/config.yaml")

    assert settings.state_store.backend == "memory"
    assert settings.state_store.redis.host == "localhost"
    assert settings.state_store.redis.port == 6379


def test_state_store_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("STATE_STORE_BACKEND", "redis")
    monkeypatch.setenv("STATE_STORE_REDIS_DSN", "redis://localhost:6380/1")
    monkeypatch.setenv("STATE_STORE_REDIS_KEY_PREFIX", "oa:test:")

    settings = load_settings(config_path="/path/not/exists/config.yaml")

    assert settings.state_store.backend == "redis"
    assert settings.state_store.redis.dsn == "redis://localhost:6380/1"
    assert settings.state_store.redis.key_prefix == "oa:test:"
