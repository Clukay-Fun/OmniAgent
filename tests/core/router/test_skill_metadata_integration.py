from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.router.router import SkillRouter  # noqa: E402


def test_router_loads_skill_metadata_with_custom_dir(tmp_path: Path) -> None:
    query_dir = tmp_path / "query"
    query_dir.mkdir()
    (query_dir / "SKILL.md").write_text(
        """## 描述
query desc

## 触发条件
query trigger
""",
        encoding="utf-8",
    )

    router = SkillRouter(skills_config={}, skills_metadata_dir=tmp_path)

    metadata_from_alias = router.get_skill_metadata("query")
    metadata_from_class_name = router.get_skill_metadata("QuerySkill")
    routing_rows = router.get_all_skill_metadata_for_routing()

    assert metadata_from_alias is not None
    assert metadata_from_alias.description == "query desc"
    assert metadata_from_class_name is not None
    assert metadata_from_class_name.name == "query"
    assert routing_rows == [
        {
            "name": "query",
            "description": "query desc",
            "trigger_conditions": "query trigger",
        }
    ]


def test_router_reload_skill_metadata_updates_cache_and_reports_failures(tmp_path: Path) -> None:
    query_dir = tmp_path / "query"
    query_dir.mkdir()
    query_file = query_dir / "SKILL.md"
    query_file.write_text("## 描述\nfirst\n", encoding="utf-8")

    router = SkillRouter(skills_config={}, skills_metadata_dir=tmp_path)
    assert router.get_skill_metadata("query") is not None
    assert router.get_skill_metadata("query").description == "first"

    query_file.write_text("## 描述\nsecond\n", encoding="utf-8")
    broken_dir = tmp_path / "broken"
    broken_dir.mkdir()
    (broken_dir / "SKILL.md").write_bytes(b"\x80\x81")

    report = router.reload_skill_metadata()

    assert "query" in report.loaded
    assert len(report.failed) == 1
    assert report.failed[0].skill_name == "broken"
    assert router.get_skill_metadata("query") is not None
    assert router.get_skill_metadata("query").description == "second"


def test_router_initializes_when_skill_metadata_dir_missing(tmp_path: Path) -> None:
    missing_dir = tmp_path / "not-exist"

    router = SkillRouter(skills_config={}, skills_metadata_dir=missing_dir)

    assert router.get_all_skill_metadata_for_routing() == []
    assert router.get_skill_metadata("query") is None
