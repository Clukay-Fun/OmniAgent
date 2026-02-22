from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.config import load_settings


def test_midterm_memory_defaults_are_conservative() -> None:
    settings = load_settings(config_path="/path/not/exists/config.yaml")

    assert settings.agent.midterm_memory.sqlite_path == "workspace/memory/midterm_memory.sqlite3"
    assert settings.agent.midterm_memory.inject_to_llm is False
    assert settings.agent.midterm_memory.llm_recent_limit == 6
    assert settings.agent.midterm_memory.llm_max_chars == 240


def test_midterm_memory_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("MIDTERM_MEMORY_SQLITE_PATH", "workspace/memory/custom-midterm.sqlite3")
    monkeypatch.setenv("MIDTERM_MEMORY_INJECT_TO_LLM", "true")
    monkeypatch.setenv("MIDTERM_MEMORY_LLM_RECENT_LIMIT", "4")
    monkeypatch.setenv("MIDTERM_MEMORY_LLM_MAX_CHARS", "180")

    settings = load_settings(config_path="/path/not/exists/config.yaml")

    assert settings.agent.midterm_memory.sqlite_path == "workspace/memory/custom-midterm.sqlite3"
    assert settings.agent.midterm_memory.inject_to_llm is True
    assert settings.agent.midterm_memory.llm_recent_limit == 4
    assert settings.agent.midterm_memory.llm_max_chars == 180
