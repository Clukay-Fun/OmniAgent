from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.skills.metadata import (  # noqa: E402
    ReloadReport,
    SkillMetadataLoader,
    _parse_skill_md,
)


def test_parse_skill_md_complete_sections() -> None:
    content = """# Skill: query

## Description
Query records from a table.

## Trigger Conditions
User asks to search data.

## Parameters
- table_name: required
- filters: optional

## Constraints
- max 100 rows

## Example Dialogue
User: list active tasks

## System Prompt
You are a concise assistant.
"""

    metadata = _parse_skill_md("query", content)

    assert metadata.name == "query"
    assert metadata.description == "Query records from a table."
    assert metadata.trigger_conditions == "User asks to search data."
    assert metadata.parameters == ["table_name: required", "filters: optional"]
    assert metadata.constraints == ["max 100 rows"]
    assert metadata.example_dialogue == "User: list active tasks"
    assert metadata.system_prompt == "You are a concise assistant."


def test_parse_skill_md_missing_sections_is_tolerant() -> None:
    content = """# Skill: minimal

## Description
Minimal skill.
"""

    metadata = _parse_skill_md("minimal", content)

    assert metadata.name == "minimal"
    assert metadata.description == "Minimal skill."
    assert metadata.trigger_conditions == ""
    assert metadata.parameters == []
    assert metadata.constraints == []
    assert metadata.example_dialogue == ""


def test_parse_skill_md_supports_chinese_headers() -> None:
    content = """## 描述
用于查询。

## 触发条件
用户要查询。

## 参数
- table_name: required

## 约束
- read only

## 示例对话
用户：查一下今天的任务
"""

    metadata = _parse_skill_md("query", content)

    assert metadata.description == "用于查询。"
    assert metadata.trigger_conditions == "用户要查询。"
    assert metadata.parameters == ["table_name: required"]
    assert metadata.constraints == ["read only"]
    assert metadata.example_dialogue == "用户：查一下今天的任务"


def test_loader_loads_and_gets_metadata(tmp_path: Path) -> None:
    query_dir = tmp_path / "query"
    query_dir.mkdir()
    (query_dir / "SKILL.md").write_text("## Description\nQuery\n", encoding="utf-8")

    create_dir = tmp_path / "create"
    create_dir.mkdir()
    (create_dir / "SKILL.md").write_text("## Description\nCreate\n", encoding="utf-8")

    loader = SkillMetadataLoader(skills_dir=tmp_path)
    report = loader.load_all()

    assert sorted(report.loaded) == ["create", "query"]
    assert report.failed == []
    assert loader.get_metadata("query") is not None
    assert loader.get_metadata("missing") is None


def test_loader_skips_dirs_without_skill_file(tmp_path: Path) -> None:
    only_dir = tmp_path / "empty"
    only_dir.mkdir()

    loader = SkillMetadataLoader(skills_dir=tmp_path)
    report = loader.load_all()

    assert report.loaded == []
    assert report.failed == []
    assert loader.get_metadata("empty") is None


def test_loader_cache_does_not_reread_without_reload(tmp_path: Path) -> None:
    query_dir = tmp_path / "query"
    query_dir.mkdir()
    skill_file = query_dir / "SKILL.md"
    skill_file.write_text("## Description\nFirst\n", encoding="utf-8")

    loader = SkillMetadataLoader(skills_dir=tmp_path)
    loader.load_all()
    assert loader.get_metadata("query") is not None
    assert loader.get_metadata("query").description == "First"

    skill_file.write_text("## Description\nSecond\n", encoding="utf-8")

    assert loader.get_metadata("query") is not None
    assert loader.get_metadata("query").description == "First"


def test_loader_reload_refreshes_cache(tmp_path: Path) -> None:
    query_dir = tmp_path / "query"
    query_dir.mkdir()
    skill_file = query_dir / "SKILL.md"
    skill_file.write_text("## Description\nFirst\n", encoding="utf-8")

    loader = SkillMetadataLoader(skills_dir=tmp_path)
    loader.load_all()

    skill_file.write_text("## Description\nSecond\n", encoding="utf-8")
    report = loader.reload()

    assert "query" in report.loaded
    assert loader.get_metadata("query") is not None
    assert loader.get_metadata("query").description == "Second"


def test_loader_handles_nonexistent_dir(tmp_path: Path) -> None:
    loader = SkillMetadataLoader(skills_dir=tmp_path / "missing")
    report = loader.load_all()

    assert report.loaded == []
    assert report.failed == []
    assert loader.get_metadata("query") is None


def test_loader_records_file_decode_error_in_report(tmp_path: Path) -> None:
    broken_dir = tmp_path / "broken"
    broken_dir.mkdir()
    (broken_dir / "SKILL.md").write_bytes(b"\x80\x81\x82")

    loader = SkillMetadataLoader(skills_dir=tmp_path)
    report = loader.load_all()

    assert report.loaded == []
    assert len(report.failed) == 1
    assert report.failed[0].skill_name == "broken"
    assert report.failed[0].file_path.endswith("broken/SKILL.md")


def test_loader_get_all_for_routing_filters_empty_description(tmp_path: Path) -> None:
    query_dir = tmp_path / "query"
    query_dir.mkdir()
    (query_dir / "SKILL.md").write_text("## Description\nQuery\n## Trigger Conditions\nsearch\n", encoding="utf-8")

    no_desc_dir = tmp_path / "no_desc"
    no_desc_dir.mkdir()
    (no_desc_dir / "SKILL.md").write_text("## Trigger Conditions\nnone\n", encoding="utf-8")

    loader = SkillMetadataLoader(skills_dir=tmp_path)
    loader.load_all()

    rows = loader.get_all_for_routing()
    assert rows == [
        {
            "name": "query",
            "description": "Query",
            "trigger_conditions": "search",
        }
    ]


def test_loader_exposes_last_report_and_loaded_count(tmp_path: Path) -> None:
    query_dir = tmp_path / "query"
    query_dir.mkdir()
    (query_dir / "SKILL.md").write_text("## Description\nQuery\n", encoding="utf-8")

    loader = SkillMetadataLoader(skills_dir=tmp_path)
    assert loader.last_report is None

    report = loader.load_all()

    assert isinstance(report, ReloadReport)
    assert loader.last_report is not None
    assert loader.loaded_count == 1
