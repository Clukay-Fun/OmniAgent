from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = REPO_ROOT / "agent" / "feishu-agent"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from src.core.skills.multi_table_linker import MultiTableLinker  # noqa: E402
from src.core.skills.data_writer import build_default_data_writer  # noqa: E402


class FakeMCPClient:
    def __init__(self) -> None:
        self.tables = [
            {"table_id": "tbl_cases", "table_name": "案件项目总库"},
            {"table_id": "tbl_contract", "table_name": "合同管理表"},
        ]
        self.created_records: list[dict[str, Any]] = []
        self.updated_records: list[dict[str, Any]] = []
        self.fail_create = False
        self.fail_update = False

    async def call_tool(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        if name == "feishu.v1.bitable.list_tables":
            return {"tables": self.tables}

        if name == "feishu.v1.bitable.record.create":
            if self.fail_create:
                return {"success": False, "error": "mock create failed"}
            self.created_records.append(params)
            return {"success": True, "record_id": "rec_new"}

        if name == "feishu.v1.bitable.search_exact":
            # 模拟子表已存在一条可更新记录
            return {
                "records": [
                    {
                        "record_id": "rec_contract_1",
                        "fields_text": {"客户名称": params.get("value")},
                    }
                ]
            }

        if name == "feishu.v1.bitable.record.update":
            if self.fail_update:
                return {"success": False, "error": "mock update failed"}
            self.updated_records.append(params)
            return {"success": True, "record_id": params.get("record_id")}

        if name == "feishu.v1.bitable.record.delete":
            return {"success": True}

        return {"success": False, "error": f"unsupported tool: {name}"}


def _build_skills_config() -> dict[str, Any]:
    return {
        "multi_table": {
            "enabled": True,
            "links": [
                {
                    "name": "case_to_contract",
                    "enabled": True,
                    "parent_tables": ["案件项目总库"],
                    "child_table": "合同管理表",
                    "parent_key": "委托人",
                    "child_key": "客户名称",
                    "create_fields": {
                        "委托人": "客户名称",
                        "主办律师": "主办律师",
                    },
                    "update_fields": {
                        "委托人": "客户名称",
                        "主办律师": "主办律师",
                        "进展": "进展",
                    },
                    "create_if_missing_on_update": True,
                    "enable_create": True,
                    "enable_update": True,
                    "enable_delete": False,
                },
            ],
        }
    }


async def _run() -> int:
    mcp = FakeMCPClient()
    linker = MultiTableLinker(mcp, _build_skills_config(), data_writer=build_default_data_writer(mcp))

    create_sync = await linker.sync_after_create(
        parent_table_id="tbl_cases",
        parent_table_name="案件项目总库",
        parent_fields={"案号": "2026-TEST-1", "委托人": "张三", "主办律师": "李律师"},
    )
    assert create_sync["success_count"] == 1, f"create sync failed: {create_sync}"

    override = linker.resolve_query_override(
        query="这个案件的合同信息",
        current_tool="feishu.v1.bitable.search",
        params={"keyword": "合同"},
        target_table_id="tbl_contract",
        target_table_name="合同管理表",
        active_table_id="tbl_cases",
        active_table_name="案件项目总库",
        active_record={"fields_text": {"委托人": "张三", "案号": "2026-TEST-1"}},
    )
    assert override is not None, "query override not generated"
    tool_name, params = override
    assert tool_name == "feishu.v1.bitable.search_exact", f"unexpected tool: {tool_name}"
    assert params.get("field") == "客户名称", f"unexpected field: {params}"
    assert params.get("value") == "张三", f"unexpected value: {params}"

    mcp.fail_create = True
    create_fail_sync = await linker.sync_after_create(
        parent_table_id="tbl_cases",
        parent_table_name="案件项目总库",
        parent_fields={"案号": "2026-TEST-2", "委托人": "李四", "主办律师": "王律师"},
    )
    pending_create = linker.build_repair_pending(create_fail_sync)
    assert pending_create is not None, "repair payload for create not generated"
    assert pending_create.get("repair_action") == "repair_child_create", f"unexpected create repair: {pending_create}"

    mcp.fail_create = False
    mcp.fail_update = True
    update_fail_sync = await linker.sync_after_update(
        parent_table_id="tbl_cases",
        parent_table_name="案件项目总库",
        updated_fields={"进展": "已结案", "委托人": "赵六"},
        source_fields={"委托人": "赵六"},
    )
    pending_update = linker.build_repair_pending(update_fail_sync)
    assert pending_update is not None, "repair payload for update not generated"
    assert pending_update.get("repair_action") == "repair_child_update", f"unexpected update repair: {pending_update}"

    print("multi-table linker verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
