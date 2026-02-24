from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_primary_service_directories_use_new_layout() -> None:
    assert (REPO_ROOT / "apps" / "agent-host" / "src" / "main.py").exists()
    assert (REPO_ROOT / "integrations" / "feishu-mcp-server" / "src" / "main.py").exists()


def test_legacy_service_directories_removed() -> None:
    assert not (REPO_ROOT / "agent").exists()
    assert not (REPO_ROOT / "mcp").exists()


def test_compose_references_new_service_paths_only() -> None:
    compose = _read(REPO_ROOT / "deploy" / "docker" / "compose.yml")
    compose_dev = _read(REPO_ROOT / "deploy" / "docker" / "compose.dev.yml")

    assert "../../apps/agent-host" in compose
    assert "../../integrations/feishu-mcp-server" in compose
    assert "../../apps/agent-host" in compose_dev
    assert "../../integrations/feishu-mcp-server" in compose_dev

    assert "../../agent/feishu-agent" not in compose
    assert "../../mcp/mcp-feishu-server" not in compose
    assert "../../agent/feishu-agent" not in compose_dev
    assert "../../mcp/mcp-feishu-server" not in compose_dev
