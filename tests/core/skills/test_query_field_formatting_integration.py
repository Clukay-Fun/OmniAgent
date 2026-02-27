from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.skills.query import QuerySkill  # noqa: E402
from src.core.skills.data_writer import WriteResult  # noqa: E402
from src.core.types import SkillContext  # noqa: E402


class _FakeMCPClient:
    async def call_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "data.bitable.list_tables":
            return {"tables": [{"table_name": "案件项目总库", "table_id": "tbl_case_1"}]}
        if tool_name.startswith("data.bitable.search"):
            return {
                "records": [
                    {
                        "record_id": "rec_1",
                        "record_url": "https://example.com/record/1",
                        "fields_text": {
                            "委托人及联系方式": {"name": "张三"},
                            "对方当事人": "李四",
                            "金额": "1234.56",
                            "标签": [{"label": "重点"}, {"name": "本周"}],
                            "案号": "(2026)粤0101民初100号",
                            "审理法院": "深圳市南山区人民法院",
                            "程序阶段": True,
                            "附件": {"files": [{"name": "证据A.pdf"}]},
                        },
                    }
                ],
                "schema": [
                    {"name": "委托人及联系方式", "type": 11},
                    {"name": "对方当事人", "type": 1},
                    {"name": "金额", "type": 2, "type_name": "货币"},
                    {"name": "标签", "type": 4},
                    {"name": "案号", "type": 1},
                    {"name": "审理法院", "type": 1},
                    {"name": "程序阶段", "type": 7},
                    {"name": "附件", "type": 17},
                ],
                "has_more": False,
                "page_token": "",
                "total": 1,
            }
        raise AssertionError(f"Unexpected tool call: {tool_name}")


class _NoopWriter:
    async def create(self, table_id, fields, *, idempotency_key=None):
        return WriteResult(success=True, record_id="rec_noop", fields=fields)

    async def update(self, table_id, record_id, fields, *, idempotency_key=None):
        return WriteResult(success=True, record_id=record_id, fields=fields)


def test_query_skill_formats_fields_with_schema_cache() -> None:
    skill = QuerySkill(
        mcp_client=_FakeMCPClient(),
        skills_config={
            "query": {
                "display_fields": {
                    "title_left": "委托人及联系方式",
                    "title_right": "对方当事人",
                    "title_suffix": "金额",
                    "case_no": "案号",
                    "court": "审理法院",
                    "stage": "程序阶段",
                }
            }
        },
        data_writer=_NoopWriter(),
    )
    context = SkillContext(
        query="查询案件",
        extra={"table_id": "tbl_case_1", "table_name": "案件项目总库"},
    )

    result = asyncio.run(skill.execute(context))

    assert result.success is True
    assert "@张三" in result.reply_text
    assert "¥1，234.56" in result.reply_text
    assert "OK 是" in result.reply_text

    records = result.data.get("records") or []
    assert records[0]["fields_text"]["金额"] == "¥1,234.56"
    assert records[0]["fields_text"]["标签"] == "重点、本周"
    assert records[0]["fields_text"]["附件"] == "OK 证据A.pdf"

    query_meta = result.data.get("query_meta") or {}
    resolution_trace = query_meta.get("resolution_trace") or []
    assert isinstance(resolution_trace, list)
    assert resolution_trace
    assert "source" in resolution_trace[0]
    assert "status" in resolution_trace[0]
    assert "slots" in resolution_trace[0]
