from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_identity_and_soul_use_xiaojing_consistently() -> None:
    identity = _read("apps/agent-host/config/identity/IDENTITY.md")
    soul = _read("apps/agent-host/config/identity/SOUL.md")

    assert "小敬" in identity
    assert "小律" not in identity
    assert "小敬" in soul
    assert "小律" not in soul


def test_agent_host_readme_exists_and_declares_primary_entry() -> None:
    readme_path = REPO_ROOT / "apps/agent-host/README.md"
    assert readme_path.exists()

    content = readme_path.read_text(encoding="utf-8")
    assert "Feishu Agent" in content
    assert "快速开始" in content


def test_three_stage_guide_mentions_new_primary_entry_paths() -> None:
    content = _read("docs/deploy/three-stage-guide.md")

    assert "apps/agent-host" in content
    assert "integrations/feishu-mcp-server" in content


def test_workspace_default_identity_uses_xiaojing_consistently() -> None:
    content = _read("apps/agent-host/src/utils/workspace.py")

    assert "小敬" in content
    assert "小律" not in content


def test_repo_readme_directory_section_mentions_agent_host_entry() -> None:
    content = _read("README.md")

    section_start = content.index("## 目录结构（已调整）")
    section_end = content.find("\n## ", section_start + 1)
    if section_end == -1:
        section_end = len(content)

    directory_section = content[section_start:section_end]
    assert "apps/agent-host" in directory_section


def test_project_context_exists_for_human_and_ai_fast_onboarding() -> None:
    content = _read("docs/project-context.md")

    assert "apps/agent-host" in content
    assert "integrations/feishu-mcp-server" in content
    assert "ResponseRenderer" in content


def test_duplicate_module_intro_docs_are_removed() -> None:
    assert not (REPO_ROOT / "apps/agent-host/docs/agent-module-intro.md").exists()
    assert not (REPO_ROOT / "integrations/feishu-mcp-server/docs/mcp-module-intro.md").exists()
