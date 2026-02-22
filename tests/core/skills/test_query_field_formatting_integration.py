from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.skills.query import QuerySkill  # noqa: E402
from src.core.types import SkillContext  # noqa: E402


class _FakeMCPClient:
    async def call_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "feishu.v1.bitable.list_tables":
            return {"tables": [{"table_name": "案件项目总库", "table_id": "tbl_case_1"}]}
        if tool_name.startswith("feishu.v1.bitable.search"):
            return {
                "records": [
                    {
                        "record_id": "rec_1",
                        "record_url": "https://example.com/record/1",
                        "fields_text": {
                            "委托人及联系方式": {"name": "张三"},
                            "对方当事人": "李四",
                            "金额": "1234.56",
                            "案号": "(2026)粤0101民初100号",
                            "审理法院": "深圳市南山区人民法院",
                            "程序阶段": True,
                        },
                    }
                ],
                "schema": [
                    {"name": "委托人及联系方式", "type": 11},
                    {"name": "对方当事人", "type": 1},
                    {"name": "金额", "type": 2, "type_name": "货币"},
                    {"name": "案号", "type": 1},
                    {"name": "审理法院", "type": 1},
                    {"name": "程序阶段", "type": 7},
                ],
                "has_more": False,
                "page_token": "",
                "total": 1,
            }
        raise AssertionError(f"Unexpected tool call: {tool_name}")


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
    )
    context = SkillContext(
        query="查询案件",
        extra={"table_id": "tbl_case_1", "table_name": "案件项目总库"},
    )

    result = asyncio.run(skill.execute(context))

    assert result.success is True
    assert "@张三" in result.reply_text
    assert "¥1，234.56" in result.reply_text
    assert "✅" in result.reply_text

    records = result.data.get("records") or []
    assert records[0]["fields_text"]["金额"] == "¥1,234.56"
