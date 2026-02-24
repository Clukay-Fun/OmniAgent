from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time
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


def _prepare_update_pending(skill: UpdateSkill) -> dict[str, Any]:
    first = asyncio.run(
        skill.execute(
            SkillContext(
                query="请更新这条记录",
                user_id="u1",
                extra={
                    "idempotency_key": "idem-update-1",
                    "active_record": {
                        "record_id": "rec_123",
                        "fields": {"案件状态": "待处理"},
                        "fields_text": {"案件状态": "待处理"},
                    },
                    "planner_plan": {
                        "tool": "record.update",
                        "params": {
                            "record_id": "rec_123",
                            "fields": {"案件状态": "进行中"},
                        },
                    },
                },
            )
        )
    )
    assert first.success is True
    pending_action = first.data.get("pending_action")
    assert isinstance(pending_action, dict)
    return pending_action


def test_update_skill_regular_flow_returns_pending_before_write() -> None:
    writer = _FakeWriter()
    skill = UpdateSkill(mcp_client=object(), skills_config={}, data_writer=writer)
    skill._table_adapter = _FakeTableAdapter()
    skill._linker = _FakeLinker()

    pending_action = _prepare_update_pending(skill)

    assert writer.update_calls == []
    assert pending_action.get("action") == "update_record"
    assert pending_action.get("ttl_seconds") == 60


def test_update_skill_confirm_executes_write_with_idempotency_key() -> None:
    writer = _FakeWriter()
    skill = UpdateSkill(mcp_client=object(), skills_config={}, data_writer=writer)
    skill._table_adapter = _FakeTableAdapter()
    skill._linker = _FakeLinker()

    pending_action = _prepare_update_pending(skill)
    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="确认",
                user_id="u1",
                extra={"pending_action": pending_action},
            )
        )
    )

    assert result.success is True
    assert len(writer.update_calls) == 1
    assert writer.update_calls[0]["record_id"] == "rec_123"
    assert writer.update_calls[0]["table_id"] == "tbl_main"
    assert writer.update_calls[0]["idempotency_key"] == "idem-update-1"


def test_update_skill_pending_cancel_clears_without_write() -> None:
    writer = _FakeWriter()
    skill = UpdateSkill(mcp_client=object(), skills_config={}, data_writer=writer)
    skill._table_adapter = _FakeTableAdapter()
    skill._linker = _FakeLinker()

    pending_action = _prepare_update_pending(skill)
    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="取消",
                user_id="u1",
                extra={"pending_action": pending_action},
            )
        )
    )

    assert result.success is True
    assert result.data.get("clear_pending_action") is True
    assert writer.update_calls == []


def test_update_skill_diff_empty_returns_noop_without_pending() -> None:
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
                    "active_record": {
                        "record_id": "rec_123",
                        "fields": {"案件状态": "进行中"},
                        "fields_text": {"案件状态": "进行中"},
                    },
                    "planner_plan": {
                        "tool": "record.update",
                        "params": {
                            "record_id": "rec_123",
                            "fields": {"案件状态": "进行中"},
                        },
                    },
                },
            )
        )
    )

    assert result.success is True
    assert "无需更新" in result.reply_text
    assert result.data.get("pending_action") is None
    assert writer.update_calls == []


def test_update_skill_pending_timeout_clears_action() -> None:
    writer = _FakeWriter()
    skill = UpdateSkill(mcp_client=object(), skills_config={}, data_writer=writer)
    skill._table_adapter = _FakeTableAdapter()
    skill._linker = _FakeLinker()

    expired_pending = {
        "action": "update_record",
        "payload": {
            "record_id": "rec_123",
            "fields": {"案件状态": "进行中"},
            "source_fields": {"案件状态": "待处理"},
            "diff": [{"field": "案件状态", "old": "待处理", "new": "进行中"}],
            "table_id": "tbl_main",
            "table_name": "案件台账",
            "idempotency_key": "idem-update-timeout",
            "created_at": time.time() - 120,
            "pending_ttl_seconds": 60,
        },
    }
    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="确认",
                user_id="u1",
                extra={"pending_action": expired_pending},
            )
        )
    )

    assert result.success is True
    assert result.data.get("clear_pending_action") is True
    assert "超时" in result.reply_text
    assert writer.update_calls == []


def test_update_skill_close_record_confirm_injects_closed_status() -> None:
    writer = _FakeWriter()
    skill = UpdateSkill(mcp_client=object(), skills_config={}, data_writer=writer)
    skill._table_adapter = _FakeTableAdapter()
    skill._linker = _FakeLinker()

    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="确认",
                user_id="u1",
                extra={
                    "pending_action": {
                        "action": "close_record",
                        "payload": {
                            "record_id": "rec_123",
                            "fields": {},
                            "source_fields": {"案件状态": "进行中"},
                            "table_id": "tbl_main",
                            "table_name": "案件台账",
                            "idempotency_key": "idem-close-1",
                            "created_at": time.time(),
                            "pending_ttl_seconds": 60,
                        },
                    }
                },
            )
        )
    )

    assert result.success is True
    assert len(writer.update_calls) == 1
    assert writer.update_calls[0]["fields"]["案件状态"] == "已结案"
    assert result.message == "案件结案成功"


def test_update_skill_close_record_enforcement_end_uses_profile_status() -> None:
    writer = _FakeWriter()
    skill = UpdateSkill(mcp_client=object(), skills_config={}, data_writer=writer)
    skill._table_adapter = _FakeTableAdapter()
    skill._linker = _FakeLinker()

    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="确认",
                user_id="u1",
                extra={
                    "pending_action": {
                        "action": "close_record",
                        "payload": {
                            "record_id": "rec_456",
                            "fields": {},
                            "source_fields": {"案件状态": "执行中"},
                            "table_id": "tbl_main",
                            "table_name": "案件项目总库",
                            "close_semantic": "enforcement_end",
                            "idempotency_key": "idem-close-2",
                            "created_at": time.time(),
                            "pending_ttl_seconds": 60,
                        },
                    }
                },
            )
        )
    )

    assert result.success is True
    assert writer.update_calls[0]["fields"]["案件状态"] == "执行终本"


def test_update_skill_append_preview_matches_final_write() -> None:
    writer = _FakeWriter()
    skill = UpdateSkill(mcp_client=object(), skills_config={}, data_writer=writer)
    skill._table_adapter = _FakeTableAdapter()
    skill._linker = _FakeLinker()

    first = asyncio.run(
        skill.execute(
            SkillContext(
                query="把进展改为已联系法官",
                user_id="u1",
                extra={
                    "active_record": {
                        "record_id": "rec_123",
                        "fields_text": {"进展": "[2026-01-01] 已立案"},
                    }
                },
            )
        )
    )
    pending_action = first.data.get("pending_action")
    assert isinstance(pending_action, dict)
    payload = pending_action.get("payload") or {}
    diff = payload.get("diff") or []
    progress_diff = next((item for item in diff if isinstance(item, dict) and item.get("field") == "进展"), None)
    assert isinstance(progress_diff, dict)
    assert progress_diff.get("mode") == "append"

    second = asyncio.run(
        skill.execute(
            SkillContext(
                query="确认",
                user_id="u1",
                extra={"pending_action": pending_action},
            )
        )
    )
    assert second.success is True
    assert writer.update_calls
    assert writer.update_calls[0]["fields"]["进展"] == progress_diff.get("new")


def test_update_skill_query_close_default_creates_close_pending() -> None:
    writer = _FakeWriter()
    skill = UpdateSkill(mcp_client=object(), skills_config={}, data_writer=writer)
    skill._table_adapter = _FakeTableAdapter()
    skill._linker = _FakeLinker()

    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="这个案子结案了",
                user_id="u1",
                extra={
                    "active_record": {
                        "record_id": "rec_900",
                        "fields_text": {"案件状态": "进行中"},
                    }
                },
            )
        )
    )

    pending_action = result.data.get("pending_action")
    assert isinstance(pending_action, dict)
    assert pending_action.get("action") == "close_record"
    payload = pending_action.get("payload") or {}
    assert payload.get("close_semantic") == "default"
    assert payload.get("fields", {}).get("案件状态") == "已结案"


def test_update_skill_query_close_enforcement_end_creates_semantic_pending() -> None:
    writer = _FakeWriter()
    skill = UpdateSkill(mcp_client=object(), skills_config={}, data_writer=writer)
    skill._table_adapter = _FakeTableAdapter()
    skill._linker = _FakeLinker()

    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="执行不了了，终本吧",
                user_id="u1",
                extra={
                    "active_record": {
                        "record_id": "rec_901",
                        "fields_text": {"案件状态": "执行中"},
                    }
                },
            )
        )
    )

    pending_action = result.data.get("pending_action")
    assert isinstance(pending_action, dict)
    assert pending_action.get("action") == "close_record"
    payload = pending_action.get("payload") or {}
    assert payload.get("close_semantic") == "enforcement_end"
    assert payload.get("fields", {}).get("案件状态") == "执行终本"


def test_create_skill_pending_confirm_uses_payload_and_idempotency_key() -> None:
    writer = _FakeWriter()
    skill = CreateSkill(mcp_client=object(), skills_config={}, data_writer=writer)
    skill._table_adapter = _FakeTableAdapter()
    skill._linker = _FakeLinker()

    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="确认",
                user_id="u1",
                extra={
                    "pending_action": {
                        "action": "create_record",
                        "payload": {
                            "fields": {"案号": "A-010", "委托人": "甲公司", "案由": "合同纠纷"},
                            "required_fields": ["案号", "委托人", "案由"],
                            "awaiting_confirm": True,
                            "idempotency_key": "idem-create-1",
                        },
                    }
                },
            )
        )
    )

    assert result.success is True
    assert len(writer.create_calls) == 1
    assert writer.create_calls[0]["idempotency_key"] == "idem-create-1"


def test_create_skill_pending_cancel_returns_clear_flag_without_write() -> None:
    writer = _FakeWriter()
    skill = CreateSkill(mcp_client=object(), skills_config={}, data_writer=writer)
    skill._table_adapter = _FakeTableAdapter()
    skill._linker = _FakeLinker()

    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="取消",
                user_id="u1",
                extra={
                    "pending_action": {
                        "action": "create_record",
                        "payload": {
                            "fields": {"案号": "A-011"},
                            "required_fields": ["案号", "委托人", "案由"],
                            "awaiting_confirm": True,
                        },
                    }
                },
            )
        )
    )

    assert result.success is True
    assert result.data.get("clear_pending_action") is True
    assert writer.create_calls == []


def test_create_skill_pending_waiting_confirm_keeps_pending_action() -> None:
    writer = _FakeWriter()
    skill = CreateSkill(mcp_client=object(), skills_config={}, data_writer=writer)
    skill._table_adapter = _FakeTableAdapter()
    skill._linker = _FakeLinker()

    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="先这样",
                user_id="u1",
                extra={
                    "pending_action": {
                        "action": "create_record",
                        "payload": {
                            "fields": {"案号": "A-012", "委托人": "乙公司", "案由": "借款纠纷"},
                            "required_fields": ["案号", "委托人", "案由"],
                            "awaiting_confirm": True,
                            "idempotency_key": "idem-create-2",
                        },
                    }
                },
            )
        )
    )

    assert result.success is True
    pending_action = result.data.get("pending_action")
    assert isinstance(pending_action, dict)
    assert pending_action.get("action") == "create_record"
    payload = pending_action.get("payload") or {}
    assert payload.get("awaiting_confirm") is True
    assert payload.get("idempotency_key") == "idem-create-2"
    assert writer.create_calls == []


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


def test_create_skill_success_with_date_sets_create_reminder_pending_action() -> None:
    writer = _FakeWriter()
    skill = CreateSkill(mcp_client=object(), skills_config={}, data_writer=writer)
    skill._table_adapter = _FakeTableAdapter()
    skill._linker = _FakeLinker()

    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="案号:A-003，委托人:甲公司，案由:合同纠纷，开庭日:2099-01-20",
                user_id="u1",
                extra={},
            )
        )
    )

    assert result.success is True
    pending_action = result.data.get("pending_action")
    assert isinstance(pending_action, dict)
    assert pending_action.get("action") == "create_reminder"
    payload = pending_action.get("payload") or {}
    reminders = payload.get("reminders") or []
    assert isinstance(reminders, list)
    assert len(reminders) >= 1


def test_create_skill_requires_data_writer_injection() -> None:
    with pytest.raises(TypeError, match="data_writer"):
        CreateSkill(mcp_client=object(), skills_config={})


def test_update_skill_requires_data_writer_injection() -> None:
    with pytest.raises(TypeError, match="data_writer"):
        UpdateSkill(mcp_client=object(), skills_config={})


def test_multi_table_linker_requires_data_writer_injection() -> None:
    with pytest.raises(TypeError, match="data_writer"):
        MultiTableLinker(mcp_client=object(), skills_config={})
