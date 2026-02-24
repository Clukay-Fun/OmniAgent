from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_root_readme_declares_command_source_of_truth() -> None:
    content = _read("README.md")
    assert "命令以 `docs/deploy/three-stage-guide.md` 为准" in content


def test_module_readmes_declare_root_run_dev_as_authoritative() -> None:
    agent_content = _read("apps/agent-host/README.md")
    mcp_content = _read("integrations/feishu-mcp-server/README.md")

    assert "run_dev.py（根目录权威实现）" in agent_content
    assert "run_dev.py（根目录权威实现）" in mcp_content


def test_project_context_describes_dependency_layers() -> None:
    content = _read("docs/project-context.md")
    assert "`requirements.txt`（根：聚合安装）" in content
    assert "`apps/agent-host/requirements.txt`（Agent 运行依赖）" in content
    assert "`integrations/feishu-mcp-server/requirements.txt`（MCP 运行依赖）" in content


def test_three_stage_guide_mentions_local_ws_mode() -> None:
    content = _read("docs/deploy/three-stage-guide.md")
    assert "agent-ws" in content
    assert "AUTOMATION_TRIGGER_ON_NEW_RECORD_EVENT=false" in content
    assert "sync/scan" in content
