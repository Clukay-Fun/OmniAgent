from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.runtime.state.midterm_memory_store import RuleSummaryExtractor, SQLiteMidtermMemoryStore


def test_sqlite_midterm_memory_store_writes_summary_items(tmp_path: Path) -> None:
    db_path = tmp_path / "midterm.sqlite3"
    store = SQLiteMidtermMemoryStore(db_path=str(db_path))
    extractor = RuleSummaryExtractor(max_keywords=3)

    items = extractor.build_items(
        user_text="帮我查询张三合同进展并记录风险",
        skill_name="QuerySkill",
        result_data={"total": 2},
    )
    inserted = store.write_items(user_id="u-midterm", items=items)

    rows = store.list_recent(user_id="u-midterm", limit=20)

    assert inserted >= 2
    assert any(row["kind"] == "keyword" for row in rows)
    assert any(row["kind"] == "event" and row["value"] == "skill:QuerySkill" for row in rows)
    assert any(row["kind"] == "event" and row["metadata"].get("total") == 2 for row in rows)
