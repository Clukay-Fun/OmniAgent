from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.capabilities.skills.implementations.chitchat import ChitchatSkill  # noqa: E402


def test_system_prompt_prefers_skill_md(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    skill_file = tmp_path / "config" / "skills" / "chitchat" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        """## 描述
测试

## System Prompt
from skill metadata
""",
        encoding="utf-8",
    )

    prompts_file = tmp_path / "config" / "prompts.yaml"
    prompts_file.parent.mkdir(parents=True, exist_ok=True)
    prompts_file.write_text(
        """chitchat:
  system: |
    from prompts yaml
""",
        encoding="utf-8",
    )

    skill = ChitchatSkill(skills_config={"chitchat": {"allow_llm": False}}, llm_client=None)

    assert skill._system_prompt == "from skill metadata"


def test_system_prompt_falls_back_to_prompts_yaml(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    skill_file = tmp_path / "config" / "skills" / "chitchat" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        """## 描述
only description
""",
        encoding="utf-8",
    )

    prompts_file = tmp_path / "config" / "prompts.yaml"
    prompts_file.parent.mkdir(parents=True, exist_ok=True)
    prompts_file.write_text(
        """chitchat:
  system: |
    from prompts yaml
""",
        encoding="utf-8",
    )

    skill = ChitchatSkill(skills_config={"chitchat": {"allow_llm": False}}, llm_client=None)

    assert skill._system_prompt == "from prompts yaml\n"


def test_system_prompt_uses_prompts_yaml_when_skill_file_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        ChitchatSkill,
        "_resolve_config_path",
        staticmethod(
            lambda *relative_paths: (
                tmp_path / "missing-skills"
                if "config/skills" in relative_paths
                else next(
                    (
                        tmp_path / str(item)
                        for item in relative_paths
                        if (tmp_path / str(item)).exists()
                    ),
                    tmp_path / str(relative_paths[0]),
                )
            )
        ),
    )

    prompts_file = tmp_path / "config" / "prompts.yaml"
    prompts_file.parent.mkdir(parents=True, exist_ok=True)
    prompts_file.write_text(
        """chitchat:
  system: |
    prompts only
""",
        encoding="utf-8",
    )

    skill = ChitchatSkill(skills_config={"chitchat": {"allow_llm": False}}, llm_client=None)

    assert skill._system_prompt == "prompts only\n"


def test_system_prompt_uses_default_when_no_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        ChitchatSkill,
        "_resolve_config_path",
        staticmethod(lambda *_paths: tmp_path / "missing"),
    )

    skill = ChitchatSkill(skills_config={"chitchat": {"allow_llm": False}}, llm_client=None)

    assert "友好、智能的助理" in skill._system_prompt
