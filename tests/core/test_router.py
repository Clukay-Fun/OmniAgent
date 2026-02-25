from __future__ import annotations

import asyncio
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.router.router import SkillRouter
from src.core.types import SkillContext, SkillExecutionStatus, SkillResult
import src.utils.metrics as metrics_module


def _build_router(timeout_seconds: float) -> SkillRouter:
    return SkillRouter(
        skills_config={
            "defaults": {"timeout_seconds": timeout_seconds},
            "skills": {"query": {"timeout_seconds": timeout_seconds}},
        }
    )


def _build_context() -> SkillContext:
    return SkillContext(query="测试", user_id="u-test")


def test_execute_skill_succeeds_within_timeout(monkeypatch) -> None:
    statuses: list[str] = []

    def _record(_skill_name: str, status: str, _duration: float) -> None:
        statuses.append(status)

    monkeypatch.setattr(metrics_module, "record_skill_execution", _record)

    class _FastSkill:
        name = "QuerySkill"

        async def execute(self, _context: SkillContext) -> SkillResult:
            await asyncio.sleep(0.01)
            return SkillResult(success=True, skill_name="QuerySkill", reply_text="ok")

    router = _build_router(timeout_seconds=0.2)
    router.register(_FastSkill())

    result = asyncio.run(router._execute_skill("query", _build_context()))

    assert result.success is True
    assert result.skill_name == "QuerySkill"
    assert statuses == [SkillExecutionStatus.SUCCESS.value]


def test_execute_skill_timeout_returns_standard_failure(monkeypatch) -> None:
    statuses: list[str] = []

    def _record(_skill_name: str, status: str, _duration: float) -> None:
        statuses.append(status)

    monkeypatch.setattr(metrics_module, "record_skill_execution", _record)

    class _SlowSkill:
        name = "QuerySkill"

        async def execute(self, _context: SkillContext) -> SkillResult:
            await asyncio.sleep(0.05)
            return SkillResult(success=True, skill_name="QuerySkill", reply_text="ok")

    router = _build_router(timeout_seconds=0.01)
    router.register(_SlowSkill())

    result = asyncio.run(router._execute_skill("query", _build_context()))

    assert result.success is False
    assert result.skill_name == "QuerySkill"
    assert "超时" in result.reply_text
    assert statuses == [SkillExecutionStatus.TIMEOUT.value]


def test_execute_skill_timeout_cleans_side_effects(monkeypatch) -> None:
    statuses: list[str] = []

    def _record(_skill_name: str, status: str, _duration: float) -> None:
        statuses.append(status)

    monkeypatch.setattr(metrics_module, "record_skill_execution", _record)
    shared_state = {"active_jobs": 0, "committed": False}

    class _SideEffectSkill:
        name = "QuerySkill"

        async def execute(self, _context: SkillContext) -> SkillResult:
            shared_state["active_jobs"] += 1
            try:
                await asyncio.sleep(0.05)
                shared_state["committed"] = True
                return SkillResult(success=True, skill_name="QuerySkill", reply_text="ok")
            finally:
                shared_state["active_jobs"] -= 1

    router = _build_router(timeout_seconds=0.01)
    router.register(_SideEffectSkill())

    result = asyncio.run(router._execute_skill("query", _build_context()))

    assert result.success is False
    assert shared_state["active_jobs"] == 0
    assert shared_state["committed"] is False
    assert statuses == [SkillExecutionStatus.TIMEOUT.value]
