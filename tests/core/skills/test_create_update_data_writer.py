from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.skills.create import CreateSkill  # noqa: E402
from src.core.skills.data_writer import WriteResult  # noqa: E402
from src.core.skills.multi_table_linker import MultiTableLinker  # noqa: E402
from src.core.skills.update import UpdateSkill  # noqa: E402
from src.core.types import SkillContext  # noqa: E402


class _FakeWriter:
    def __init__(self, *, create_result: WriteResult | None = None, update_result: WriteResult | None = None) -> None:
        self.create_result = create_result or WriteResult(success=True, record_id="rec_create", record_url="https://example/create")
        self.update_result = update_result or WriteResult(success=True, record_id="rec_update", record_url="https://example/update")
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []

    async def create(
        self,
        table_id: str | None,
        fields: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
        self.create_calls.append({"table_id": table_id, "fields": fields, "idempotency_key": idempotency_key})
        return self.create_result

    async def update(
        self,
        table_id: str | None,
        record_id: str,
        fields: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
        self.update_calls.append(
            {
                "table_id": table_id,
                "record_id": record_id,
                "fields": fields,
                "idempotency_key": idempotency_key,
            }
        )
        return self.update_result


class _FakeTableAdapter:
    async def resolve_table_context(self, query: str, extra: dict[str, Any] | None, last_result: dict[str, Any] | None) -> Any:
        return SimpleNamespace(table_id="tbl_main", table_name="案件台账")

    async def adapt_fields_for_table(self, fields: dict[str, Any], table_id: str | None) -> tuple[dict[str, Any], list[str], list[str]]:
        return fields, [], []

    def build_field_not_found_message(self, unresolved: list[str], available: list[str], table_name: str | None) -> str:
        return "字段不匹配"

    def extract_table_id_from_record(self, record: dict[str, Any] | None) -> str | None:
        return None


class _FakeLinker:
    async def sync_after_create(self, **kwargs: Any) -> dict[str, Any]:
        return {"success_count": 0, "failures": []}

    async def sync_after_update(self, **kwargs: Any) -> dict[str, Any]:
        return {"success_count": 0, "failures": []}

    def summarize(self, sync_result: dict[str, Any]) -> str:
        return ""

    def build_repair_pending(self, sync_result: dict[str, Any]) -> dict[str, Any] | None:
        return None


def test_create_skill_success_uses_data_writer() -> None:
    writer = _FakeWriter()
    skill = CreateSkill(mcp_client=object(), skills_config={}, data_writer=writer)
    skill._table_adapter = _FakeTableAdapter()
    skill._linker = _FakeLinker()

    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="案号:A-001，委托人:甲公司，案由:合同纠纷",
                user_id="u1",
                extra={},
            )
        )
    )

    assert result.success is True
    assert len(writer.create_calls) == 1
    assert writer.create_calls[0]["table_id"] == "tbl_main"
    assert writer.create_calls[0]["fields"]["案号"] == "A-001"
    assert result.data.get("record_id") == "rec_create"


def test_update_skill_success_uses_data_writer() -> None:
    writer = _FakeWriter()
    skill = UpdateSkill(mcp_client=object(), skills_config={}, data_writer=writer)
    skill._table_adapter = _FakeTableAdapter()
    skill._linker = _FakeLinker()

    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="请更新这条记录",
                user_id="u1",
                extra={
                    "planner_plan": {
                        "tool": "record.update",
                        "params": {
                            "record_id": "rec_123",
                            "fields": {"案件状态": "进行中"},
                        },
                    }
                },
            )
        )
    )

    assert result.success is True
    assert len(writer.update_calls) == 1
    assert writer.update_calls[0]["record_id"] == "rec_123"
    assert writer.update_calls[0]["table_id"] == "tbl_main"


def test_create_skill_writer_failure_returns_skill_failure() -> None:
    writer = _FakeWriter(create_result=WriteResult(success=False, error="boom"))
    skill = CreateSkill(mcp_client=object(), skills_config={}, data_writer=writer)
    skill._table_adapter = _FakeTableAdapter()
    skill._linker = _FakeLinker()

    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="案号:A-002，委托人:乙公司，案由:劳动纠纷",
                user_id="u1",
                extra={},
            )
        )
    )

    assert result.success is False
    assert "boom" in result.message


def test_create_skill_requires_data_writer_injection() -> None:
    with pytest.raises(TypeError, match="data_writer"):
        CreateSkill(mcp_client=object(), skills_config={})


def test_update_skill_requires_data_writer_injection() -> None:
    with pytest.raises(TypeError, match="data_writer"):
        UpdateSkill(mcp_client=object(), skills_config={})


def test_multi_table_linker_requires_data_writer_injection() -> None:
    with pytest.raises(TypeError, match="data_writer"):
        MultiTableLinker(mcp_client=object(), skills_config={})
