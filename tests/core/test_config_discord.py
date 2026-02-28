from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.config import load_settings


def test_discord_config_defaults() -> None:
    settings = load_settings(config_path="/path/not/exists/config.yaml")

    assert settings.discord.enabled is False
    assert settings.discord.bot_token == ""
    assert settings.discord.require_mention is True
    assert settings.discord.allow_bots is False
    assert settings.discord.private_chat_only is False
    assert settings.discord.text_chunk_limit == 1800
    assert settings.discord.max_lines_per_message == 30
    assert settings.discord.embed_enabled is True
    assert settings.discord.components_enabled is True


def test_discord_config_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_ENABLED", "true")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token_123")
    monkeypatch.setenv("DISCORD_REQUIRE_MENTION", "false")
    monkeypatch.setenv("DISCORD_ALLOW_BOTS", "true")
    monkeypatch.setenv("DISCORD_PRIVATE_CHAT_ONLY", "true")
    monkeypatch.setenv("DISCORD_TEXT_CHUNK_LIMIT", "1200")
    monkeypatch.setenv("DISCORD_MAX_LINES_PER_MESSAGE", "18")
    monkeypatch.setenv("DISCORD_EMBED_ENABLED", "false")
    monkeypatch.setenv("DISCORD_COMPONENTS_ENABLED", "false")

    settings = load_settings(config_path="/path/not/exists/config.yaml")

    assert settings.discord.enabled is True
    assert settings.discord.bot_token == "token_123"
    assert settings.discord.require_mention is False
    assert settings.discord.allow_bots is True
    assert settings.discord.private_chat_only is True
    assert settings.discord.text_chunk_limit == 1200
    assert settings.discord.max_lines_per_message == 18
    assert settings.discord.embed_enabled is False
    assert settings.discord.components_enabled is False
