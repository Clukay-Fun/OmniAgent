from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.feishu.card_template_config import (
    is_template_enabled,
    resolve_template_version,
    reset_template_config_cache,
)


def test_template_config_loads_from_yaml(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "card_templates.yaml"
    config_path.write_text(
        """
default_versions:
  query.list: v9
enabled:
  query.list.v9: true
  query.list.v1: false
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("CARD_TEMPLATE_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CARD_TEMPLATE_CONFIG_YAML_ENABLED", "true")
    reset_template_config_cache()

    assert resolve_template_version("query.list") == "v9"
    assert is_template_enabled("query.list", "v9") is True
    assert is_template_enabled("query.list", "v1") is False


def test_template_config_falls_back_when_yaml_missing(monkeypatch) -> None:
    monkeypatch.setenv("CARD_TEMPLATE_CONFIG_PATH", "__missing__/card_templates.yaml")
    monkeypatch.setenv("CARD_TEMPLATE_CONFIG_YAML_ENABLED", "true")
    reset_template_config_cache()

    assert resolve_template_version("query.list") == "v1"
    assert is_template_enabled("query.list", "v1") is True
    assert is_template_enabled("query.list", "v2") is True


def test_template_config_falls_back_when_yaml_invalid(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "card_templates.yaml"
    config_path.write_text("default_versions: [", encoding="utf-8")

    monkeypatch.setenv("CARD_TEMPLATE_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CARD_TEMPLATE_CONFIG_YAML_ENABLED", "true")
    reset_template_config_cache()

    assert resolve_template_version("query.list") == "v1"
    assert is_template_enabled("query.list", "v1") is True


def test_template_config_can_disable_yaml_loading(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "card_templates.yaml"
    config_path.write_text(
        """
default_versions:
  query.list: v99
enabled:
  query.list.v99: true
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("CARD_TEMPLATE_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CARD_TEMPLATE_CONFIG_YAML_ENABLED", "false")
    reset_template_config_cache()

    assert resolve_template_version("query.list") == "v1"
    assert is_template_enabled("query.list", "v99") is False
