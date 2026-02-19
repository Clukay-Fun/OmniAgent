from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_lines(relative_path: str) -> list[str]:
    path = REPO_ROOT / relative_path
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_service_requirements_are_runtime_only() -> None:
    agent_lines = _read_lines("apps/agent-host/requirements.txt")
    mcp_lines = _read_lines("integrations/feishu-mcp-server/requirements.txt")

    assert all(not line.startswith("pytest") for line in agent_lines)
    assert all(not line.startswith("pytest") for line in mcp_lines)


def test_root_requirements_aggregates_services_and_dev_tools() -> None:
    root_lines = _read_lines("requirements.txt")

    assert "-r apps/agent-host/requirements.txt" in root_lines
    assert "-r integrations/feishu-mcp-server/requirements.txt" in root_lines
    assert "-r requirements/dev.txt" in root_lines
