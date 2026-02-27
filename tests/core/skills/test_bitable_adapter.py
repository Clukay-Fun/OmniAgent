from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.skills.bitable_adapter import BitableAdapter  # noqa: E402 # type: ignore[reportMissingImports]


class _FakeMCP:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.tables: list[dict[str, str]] = [
            {"table_id": "tbl_cases", "table_name": "案件项目总库"},
            {"table_id": "tbl_archive", "table_name": "归档表"},
        ]
        self.schema: list[dict[str, Any]] = [
            {"name": "案号"},
            {"name": "案件状态"},
        ]

    async def call_tool(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, params))
        if name == "feishu.v1.bitable.list_tables":
            return {"tables": self.tables}
        if name == "feishu.v1.bitable.search":
            return {"schema": self.schema}
        return {}


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


def test_map_field_name_matches_normalized_exact() -> None:
    adapter = BitableAdapter(mcp_client=object(), skills_config={})
    available = ["承办法官、助理及联系方式", "案号"]
    normalized_lookup = {"承办法官助理及联系方式": "承办法官、助理及联系方式", "案号": "案号"}

    mapped = adapter._map_field_name("承办法官 助理及联系方式", available, normalized_lookup)

    assert mapped == "承办法官、助理及联系方式"


def test_get_fields_is_backward_compatible_with_get_table_fields() -> None:
    mcp = _FakeMCP()
    adapter = BitableAdapter(mcp_client=mcp, skills_config={})

    fields_from_compat = asyncio.run(adapter.get_fields("tbl_main"))
    fields_from_primary = asyncio.run(adapter.get_table_fields("tbl_main"))

    assert fields_from_compat == ["案号", "案件状态"]
    assert fields_from_primary == fields_from_compat
    assert len(mcp.calls) == 1
    assert mcp.calls[0][0] == "feishu.v1.bitable.search"


def test_extract_from_last_result_scans_records_for_first_valid_table_id() -> None:
    adapter = BitableAdapter(mcp_client=object(), skills_config={})

    context = adapter._extract_from_last_result(
        {
            "records": [
                {"record_url": "https://x.feishu.cn/base/a?record=rec_missing_table", "app_token": "app_skip"},
                {"record_url": "https://x.feishu.cn/base/a?table=tbl_from_second&record=rec2", "app_token": "app_second"},
            ]
        }
    )

    assert context.table_id == "tbl_from_second"
    assert context.app_token == "app_second"


def test_resolve_table_context_priority_extra_then_last_result_then_query() -> None:
    mcp = _FakeMCP()
    adapter = BitableAdapter(mcp_client=mcp, skills_config={"table_aliases": {"案件项目总库": ["案件总库"]}})

    from_extra = asyncio.run(
        adapter.resolve_table_context(
            query="请在归档表里查询",
            extra={"table_id": "tbl_cases"},
            last_result={"table_id": "tbl_archive"},
        )
    )
    assert from_extra.table_id == "tbl_cases"
    assert from_extra.source == "extra"

    from_last_result = asyncio.run(
        adapter.resolve_table_context(
            query="请在归档表里查询",
            extra={},
            last_result={"table_id": "tbl_cases"},
        )
    )
    assert from_last_result.table_id == "tbl_cases"
    assert from_last_result.source == "last_result"

    from_query = asyncio.run(
        adapter.resolve_table_context(
            query="请在案件总库里查询",
            extra={},
            last_result=None,
        )
    )
    assert from_query.table_id == "tbl_cases"
    assert from_query.source == "query_alias"


def test_adapt_fields_for_table_degrades_when_table_id_missing() -> None:
    mcp = _FakeMCP()
    adapter = BitableAdapter(mcp_client=mcp, skills_config={})
    fields = {"案号": "A-1", "状态": "进行中"}

    adapted, unresolved, available = asyncio.run(adapter.adapt_fields_for_table(fields, table_id=None))

    assert adapted == fields
    assert unresolved == []
    assert available == []
    assert mcp.calls == []


def test_adapt_fields_for_table_returns_unresolved_and_available_fields() -> None:
    mcp = _FakeMCP()
    adapter = BitableAdapter(mcp_client=mcp, skills_config={})

    adapted, unresolved, available = asyncio.run(
        adapter.adapt_fields_for_table({"案号": "A-1", "状态": "进行中", "不存在字段": "x"}, table_id="tbl_cases")
    )

    assert adapted == {"案号": "A-1", "案件状态": "进行中"}
    assert unresolved == ["不存在字段"]
    assert available == ["案号", "案件状态"]


def test_schema_cache_hit_avoids_repeated_mcp_call_in_adapt_fields() -> None:
    mcp = _FakeMCP()
    adapter = BitableAdapter(mcp_client=mcp, skills_config={})

    asyncio.run(adapter.adapt_fields_for_table({"案号": "A-1"}, table_id="tbl_cases"))
    asyncio.run(adapter.adapt_fields_for_table({"案号": "A-2"}, table_id="tbl_cases"))

    search_calls = [name for name, _ in mcp.calls if name == "feishu.v1.bitable.search"]
    assert len(search_calls) == 1
