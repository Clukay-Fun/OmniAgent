from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.config import load_settings


def test_reply_feature_flags_default_to_conservative() -> None:
    settings = load_settings(config_path="/path/not/exists/config.yaml")

    assert settings.reply.query_card_v2_enabled is False
    assert settings.reply.reply_personalization_enabled is False


def test_reply_feature_flags_support_env_override(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_CARD_V2_ENABLED", "true")
    monkeypatch.setenv("REPLY_PERSONALIZATION_ENABLED", "true")

    settings = load_settings(config_path="/path/not/exists/config.yaml")

    assert settings.reply.query_card_v2_enabled is True
    assert settings.reply.reply_personalization_enabled is True
