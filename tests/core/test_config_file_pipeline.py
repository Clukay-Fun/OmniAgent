from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.config import load_settings


def test_file_pipeline_defaults_safe() -> None:
    settings = load_settings(config_path="/path/not/exists/config.yaml")

    assert settings.file_pipeline.enabled is False
    assert settings.file_extractor.enabled is False
    assert settings.file_extractor.provider == "none"
    assert settings.file_extractor.fail_open is True
    assert settings.file_context.injection_enabled is False


def test_file_pipeline_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("FILE_PIPELINE_ENABLED", "true")
    monkeypatch.setenv("FILE_PIPELINE_MAX_BYTES", "4096")
    monkeypatch.setenv("FILE_PIPELINE_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("FILE_EXTRACTOR_ENABLED", "true")
    monkeypatch.setenv("FILE_EXTRACTOR_PROVIDER", "mineru")
    monkeypatch.setenv("FILE_EXTRACTOR_API_KEY", "k")
    monkeypatch.setenv("FILE_EXTRACTOR_API_BASE", "https://api.example.com")
    monkeypatch.setenv("FILE_CONTEXT_INJECTION_ENABLED", "true")
    monkeypatch.setenv("FILE_CONTEXT_MAX_CHARS", "999")
    monkeypatch.setenv("FILE_CONTEXT_MAX_TOKENS", "111")

    settings = load_settings(config_path="/path/not/exists/config.yaml")

    assert settings.file_pipeline.enabled is True
    assert settings.file_pipeline.max_bytes == 4096
    assert settings.file_pipeline.timeout_seconds == 7
    assert settings.file_extractor.enabled is True
    assert settings.file_extractor.provider == "mineru"
    assert settings.file_context.injection_enabled is True
    assert settings.file_context.max_chars == 999
    assert settings.file_context.max_tokens == 111
