from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.skills.bitable_adapter import BitableAdapter  # noqa: E402 # type: ignore[reportMissingImports]


def test_extract_table_id_from_record_prefers_direct_field() -> None:
    adapter = BitableAdapter(mcp_client=object(), skills_config={})

    table_id = adapter.extract_table_id_from_record({"table_id": "tbl_direct", "record_url": "https://x.feishu.cn/base/a?table=tbl_url&record=rec"})

    assert table_id == "tbl_direct"


def test_extract_table_id_from_record_from_record_url() -> None:
    adapter = BitableAdapter(mcp_client=object(), skills_config={})

    table_id = adapter.extract_table_id_from_record({"record_url": "https://x.feishu.cn/base/a?table=tbl_url&record=rec"})

    assert table_id == "tbl_url"


def test_extract_from_extra_uses_active_table_and_pending_payload() -> None:
    adapter = BitableAdapter(mcp_client=object(), skills_config={})

    from_active = adapter._extract_from_extra({"active_table_id": "tbl_active", "active_table_name": "案件项目总库"})
    assert from_active.table_id == "tbl_active"
    assert from_active.table_name == "案件项目总库"

    from_pending = adapter._extract_from_extra(
        {
            "pending_action": {
                "action": "update_record",
                "payload": {"table_id": "tbl_pending", "table_name": "案件项目总库"},
            }
        }
    )
    assert from_pending.table_id == "tbl_pending"
    assert from_pending.table_name == "案件项目总库"


def test_map_field_name_matches_exact_without_changes() -> None:
    adapter = BitableAdapter(mcp_client=object(), skills_config={})
    available = ["案件状态", "案号"]

    mapped = adapter._map_field_name("案件状态", available, {})

    assert mapped == "案件状态"


def test_map_field_name_matches_after_space_removed() -> None:
    adapter = BitableAdapter(mcp_client=object(), skills_config={})
    available = ["案件状态", "案号"]

    mapped = adapter._map_field_name("案件 状态", available, {})

    assert mapped == "案件状态"


def test_map_field_name_suffix_match_requires_uniqueness() -> None:
    adapter = BitableAdapter(mcp_client=object(), skills_config={})

    unique = adapter._map_field_name("状态", ["任务状态", "案号"], {})
    ambiguous = adapter._map_field_name("状态", ["任务状态", "案件状态"], {})

    assert unique == "任务状态"
    assert ambiguous is None


def test_map_field_name_contains_match_requires_uniqueness() -> None:
    adapter = BitableAdapter(mcp_client=object(), skills_config={})

    unique = adapter._map_field_name("相关", ["相关人", "案号"], {})
    ambiguous = adapter._map_field_name("相关", ["相关人", "案件相关人"], {})

    assert unique == "相关人"
    assert ambiguous is None


def test_map_field_name_keeps_alias_candidate_exact_mapping() -> None:
    adapter = BitableAdapter(mcp_client=object(), skills_config={})

    mapped = adapter._map_field_name("被告", ["对方当事人", "案号"], {})

    assert mapped == "对方当事人"
