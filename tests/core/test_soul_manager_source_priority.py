from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.expression.soul.soul import SoulManager  # noqa: E402


def test_soul_manager_prefers_config_identity_files(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    manager = SoulManager(workspace_root=workspace_root, reload_interval=0)

    config_root = tmp_path / "config" / "identity"
    config_root.mkdir(parents=True)
    (config_root / "SOUL.md").write_text("config soul", encoding="utf-8")
    (config_root / "IDENTITY.md").write_text("config identity", encoding="utf-8")

    (workspace_root / "SOUL.md").write_text("workspace soul", encoding="utf-8")
    (workspace_root / "IDENTITY.md").write_text("workspace identity", encoding="utf-8")

    manager._config_soul_path = config_root / "SOUL.md"
    manager._config_identity_path = config_root / "IDENTITY.md"
    manager._load(force=True)

    assert manager.get_soul() == "config soul"
    assert manager.get_identity() == "config identity"


def test_soul_manager_falls_back_to_workspace_when_config_missing(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    manager = SoulManager(workspace_root=workspace_root, reload_interval=0)

    (workspace_root / "SOUL.md").write_text("workspace soul", encoding="utf-8")
    (workspace_root / "IDENTITY.md").write_text("workspace identity", encoding="utf-8")

    manager._config_soul_path = tmp_path / "missing" / "SOUL.md"
    manager._config_identity_path = tmp_path / "missing" / "IDENTITY.md"
    manager._load(force=True)

    assert manager.get_soul() == "workspace soul"
    assert manager.get_identity() == "workspace identity"
