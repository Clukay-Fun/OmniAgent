from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.skills.action_execution_service import ActionExecutionService  # noqa: E402
from src.core.skills.data_writer import WriteResult  # noqa: E402


class _FakeWriter:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []

    async def create(self, table_id: str | None, fields: dict[str, Any], *, idempotency_key: str | None = None) -> WriteResult:
        self.create_calls.append({"table_id": table_id, "fields": fields, "idempotency_key": idempotency_key})
        return WriteResult(success=True, record_id="rec_new", record_url="https://example/new")

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
        return WriteResult(success=True, record_id=record_id, record_url="https://example/update")

    async def delete(self, table_id: str | None, record_id: str, *, idempotency_key: str | None = None) -> WriteResult:
        self.delete_calls.append({"table_id": table_id, "record_id": record_id, "idempotency_key": idempotency_key})
        return WriteResult(success=True, record_id=record_id)


class _FakeLinker:
    async def sync_after_create(self, **_kwargs: Any) -> dict[str, Any]:
        return {"success_count": 0, "failures": []}

    async def sync_after_update(self, **_kwargs: Any) -> dict[str, Any]:
        return {"success_count": 0, "failures": []}

    async def sync_after_delete(self, **_kwargs: Any) -> dict[str, Any]:
        return {"success_count": 0, "failures": []}

    def summarize(self, _sync_result: dict[str, Any]) -> str:
        return ""

    def build_repair_pending(self, _sync_result: dict[str, Any]) -> dict[str, Any] | None:
        return None


def test_apply_update_rules_appends_progress_with_date_prefix() -> None:
    service = ActionExecutionService(data_writer=_FakeWriter(), linker=_FakeLinker())
    result = service.apply_update_rules(
        "案件项目总库",
        {"进展": "已联系法官"},
        {"进展": "[2026-01-01] 已立案"},
        append_date="2026-02-23",
    )

    assert "[2026-01-01] 已立案" in result["进展"]
    assert "已联系法官" in result["进展"]
    assert "[2026-02-23]" in result["进展"]


def test_build_update_preview_marks_append_mode() -> None:
    service = ActionExecutionService(data_writer=_FakeWriter(), linker=_FakeLinker())
    effective, diff_items, append_date = service.build_update_preview(
        table_name="案件项目总库",
        fields={"进展": "已联系法官"},
        source_fields={"进展": "[2026-01-01] 已立案"},
        append_date="2026-02-23",
    )

    assert append_date == "2026-02-23"
    assert effective["进展"].endswith("[2026-02-23] 已联系法官")
    assert diff_items[0]["mode"] == "append"
    assert "已联系法官" in diff_items[0]["delta"]


def test_create_defaults_and_smart_inference_are_applied() -> None:
    writer = _FakeWriter()
    service = ActionExecutionService(data_writer=writer, linker=_FakeLinker())

    outcome = asyncio.run(
        service.execute_create(
            table_id="tbl_case",
            table_name="案件项目总库",
            fields={"案号": "(2026)粤01执123号"},
            idempotency_key="idem-1",
        )
    )

    assert outcome.success is True
    sent_fields = writer.create_calls[0]["fields"]
    assert sent_fields["案件状态"] == "未结"
    assert sent_fields["程序阶段"] == "执行"


def test_duplicate_field_mapping_is_configurable() -> None:
    service = ActionExecutionService(data_writer=_FakeWriter(), linker=_FakeLinker())
    assert service.resolve_duplicate_field_name("案件项目总库") == "案号"
    assert service.resolve_duplicate_field_name("合同管理表") == "合同号"
    assert service.resolve_duplicate_field_name("招投标台账") == "项目号"


def test_team_overview_is_hard_blocked_for_all_writes() -> None:
    writer = _FakeWriter()
    service = ActionExecutionService(data_writer=writer, linker=_FakeLinker())

    create_result = asyncio.run(
        service.execute_create(
            table_id="tbl_ro",
            table_name="团队成员工作总览（只读）",
            fields={"任务描述": "foo"},
            idempotency_key=None,
        )
    )
    update_result = asyncio.run(
        service.execute_update(
            action_name="update_record",
            table_id="tbl_ro",
            table_name="团队成员工作总览（只读）",
            record_id="rec_1",
            fields={"任务状态": "完成"},
            source_fields={"任务状态": "进行中"},
            idempotency_key=None,
        )
    )
    delete_result = asyncio.run(
        service.execute_delete(
            table_id="tbl_ro",
            table_name="团队成员工作总览（只读）",
            record_id="rec_1",
            case_no="N/A",
            idempotency_key=None,
        )
    )

    assert create_result.success is False
    assert update_result.success is False
    assert delete_result.success is False
    assert writer.create_calls == []
    assert writer.update_calls == []
    assert writer.delete_calls == []


def test_close_semantic_resolves_from_config_keywords() -> None:
    service = ActionExecutionService(data_writer=_FakeWriter(), linker=_FakeLinker())

    assert service.resolve_close_semantic("这个案子结案了", "案件项目总库") == "default"
    assert service.resolve_close_semantic("判决生效了", "案件项目总库") == "default"
    assert service.resolve_close_semantic("执行不了了，终本吧", "案件项目总库") == "enforcement_end"
    assert service.resolve_close_semantic("终结本次执行", "案件项目总库") == "enforcement_end"


def test_build_pending_close_action_data_uses_profile_rules() -> None:
    service = ActionExecutionService(data_writer=_FakeWriter(), linker=_FakeLinker())
    data = service.build_pending_close_action_data(
        record_id="rec_1",
        fields={},
        source_fields={"案件状态": "执行中"},
        diff_items=[],
        table_id="tbl_case",
        table_name="案件项目总库",
        idempotency_key="idem-close",
        created_at=1.0,
        ttl_seconds=60,
        append_date="2026-02-23",
        intent_text="执行终本",
    )

    payload = data["pending_action"]["payload"]
    assert payload["close_semantic"] == "enforcement_end"
    assert payload["close_status_value"] == "执行终本"
    assert payload["close_remove_from_open_list"] is False
    assert payload["close_reminder_policy"] == "preserve_seizure"
