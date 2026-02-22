from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.skills.data_writer import WriteResult  # noqa: E402
from src.core.skills.delete import DeleteSkill  # noqa: E402
from src.core.types import SkillContext  # noqa: E402


class _FakeWriter:
    def __init__(self) -> None:
        self.delete_calls: list[dict[str, Any]] = []

    async def delete(self, table_id: str | None, record_id: str, *, idempotency_key: str | None = None) -> WriteResult:
        self.delete_calls.append({"table_id": table_id, "record_id": record_id, "idempotency_key": idempotency_key})
        return WriteResult(success=True, record_id=record_id)


class _FakeMCP:
    async def call_tool(self, _name: str, _params: dict[str, Any]) -> dict[str, Any]:
        return {"success": True, "records": []}


class _FakeLinker:
    async def sync_after_delete(self, **_kwargs: Any) -> dict[str, Any]:
        return {"success_count": 0, "failures": []}

    def summarize(self, _sync: dict[str, Any]) -> str:
        return ""


def test_delete_skill_disabled_by_default(monkeypatch) -> None:
    monkeypatch.setenv("CRUD_DELETE_ENABLED", "false")
    skill = DeleteSkill(mcp_client=_FakeMCP(), skills_config={}, data_writer=_FakeWriter())
    skill._linker = _FakeLinker()
    result = asyncio.run(skill.execute(SkillContext(query="删除案号A-1", user_id="u1", extra={})))
    assert result.success is False
    assert "未开启" in result.reply_text


def test_delete_skill_creates_pending_action_with_60s_ttl(monkeypatch) -> None:
    monkeypatch.setenv("CRUD_DELETE_ENABLED", "true")
    skill = DeleteSkill(mcp_client=_FakeMCP(), skills_config={}, data_writer=_FakeWriter())
    skill._linker = _FakeLinker()
    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="删除这个案件",
                user_id="u1",
                extra={"active_record": {"record_id": "rec_1", "fields_text": {"案号": "A-1"}}},
                last_result={},
            )
        )
    )
    pending_action = result.data.get("pending_action")
    assert isinstance(pending_action, dict)
    assert pending_action.get("action") == "delete_record"
    assert pending_action.get("ttl_seconds") == 60


def test_delete_skill_callback_confirm_executes_delete(monkeypatch) -> None:
    monkeypatch.setenv("CRUD_DELETE_ENABLED", "true")
    writer = _FakeWriter()
    skill = DeleteSkill(mcp_client=_FakeMCP(), skills_config={}, data_writer=writer)
    skill._linker = _FakeLinker()
    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="",
                user_id="u1",
                extra={
                    "callback_intent": "confirm",
                    "pending_action": {
                        "action": "delete_record",
                        "payload": {"record_id": "rec_2", "case_no": "A-2", "table_id": "tbl_1"},
                    },
                },
            )
        )
    )
    assert result.success is True
    assert len(writer.delete_calls) == 1
    assert writer.delete_calls[0]["record_id"] == "rec_2"


def test_delete_skill_requires_full_confirm_phrase(monkeypatch) -> None:
    monkeypatch.setenv("CRUD_DELETE_ENABLED", "true")
    writer = _FakeWriter()
    skill = DeleteSkill(mcp_client=_FakeMCP(), skills_config={}, data_writer=writer)
    skill._linker = _FakeLinker()
    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="删除",
                user_id="u1",
                extra={
                    "pending_action": {
                        "action": "delete_record",
                        "payload": {"record_id": "rec_2", "case_no": "A-2", "table_id": "tbl_1"},
                    }
                },
            )
        )
    )
    assert result.success is False
    assert len(writer.delete_calls) == 0


def test_delete_skill_passes_idempotency_key(monkeypatch) -> None:
    monkeypatch.setenv("CRUD_DELETE_ENABLED", "true")
    writer = _FakeWriter()
    skill = DeleteSkill(mcp_client=_FakeMCP(), skills_config={}, data_writer=writer)
    skill._linker = _FakeLinker()
    result = asyncio.run(
        skill.execute(
            SkillContext(
                query="确认删除",
                user_id="u1",
                extra={
                    "pending_action": {
                        "action": "delete_record",
                        "payload": {"record_id": "rec_2", "case_no": "A-2", "table_id": "tbl_1"},
                    }
                },
            )
        )
    )
    assert result.success is True
    assert len(writer.delete_calls) == 1
    assert writer.delete_calls[0]["idempotency_key"].startswith("delete-")
