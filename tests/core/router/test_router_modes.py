from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.intent import IntentResult, SkillMatch  # noqa: E402 # type: ignore[reportMissingImports]
from src.core.router.llm_selector import LLMSelectionResult  # noqa: E402 # type: ignore[reportMissingImports]
from src.core.router.router import SkillRouter  # noqa: E402 # type: ignore[reportMissingImports]
from src.core.types import SkillContext, SkillResult  # noqa: E402 # type: ignore[reportMissingImports]


def _build_intent(skill_name: str = "QuerySkill") -> IntentResult:
    return IntentResult(
        skills=[SkillMatch(name=skill_name, score=0.9, reason="test")],
        is_chain=False,
        requires_llm_confirm=False,
        method="rule",
    )


def _build_context(query: str = "测试消息") -> SkillContext:
    return SkillContext(query=query, user_id="u-test")


def _build_router(mode: str) -> SkillRouter:
    return SkillRouter(skills_config={"routing": {"mode": mode, "shadow_max_pending": 10}})


async def _drain_shadow_tasks(router: SkillRouter) -> None:
    if not router._shadow_tasks:
        return
    await asyncio.gather(*list(router._shadow_tasks))


def test_rule_mode_uses_existing_logic() -> None:
    router = _build_router("rule")
    called = {"rule": 0, "llm": 0}

    async def _fake_rule(_intent: IntentResult, _context: SkillContext) -> SkillResult:
        called["rule"] += 1
        return SkillResult(success=True, skill_name="QuerySkill", reply_text="rule")

    class _Selector:
        async def select(self, _user_message: str, _context: Any = None):
            called["llm"] += 1
            return None

    router._llm_selector = _Selector()
    router._rule_based_route = _fake_rule

    result = asyncio.run(router.route(_build_intent(), _build_context()))

    assert result.skill_name == "QuerySkill"
    assert called["rule"] == 1
    assert called["llm"] == 0


def test_shadow_mode_returns_rule_result() -> None:
    router = _build_router("shadow")

    async def _fake_rule(_intent: IntentResult, _context: SkillContext) -> SkillResult:
        return SkillResult(success=True, skill_name="QuerySkill", reply_text="rule")

    async def _fake_shadow_compare(*, user_message: str, context: SkillContext, rule_skill_name: str) -> None:
        _ = (user_message, context, rule_skill_name)
        return None

    router._rule_based_route = _fake_rule
    router._shadow_llm_compare = _fake_shadow_compare
    router._llm_selector = object()

    result = asyncio.run(router.route(_build_intent(), _build_context()))

    asyncio.run(_drain_shadow_tasks(router))
    assert result.skill_name == "QuerySkill"
    assert result.reply_text == "rule"


def test_shadow_mode_triggers_llm_comparison() -> None:
    router = _build_router("shadow")
    called = {"shadow": 0}

    async def _fake_rule(_intent: IntentResult, _context: SkillContext) -> SkillResult:
        return SkillResult(success=True, skill_name="QuerySkill", reply_text="rule")

    async def _fake_shadow_compare(*, user_message: str, context: SkillContext, rule_skill_name: str) -> None:
        _ = (user_message, context, rule_skill_name)
        called["shadow"] += 1

    router._rule_based_route = _fake_rule
    router._shadow_llm_compare = _fake_shadow_compare
    router._llm_selector = object()

    asyncio.run(router.route(_build_intent(), _build_context()))
    asyncio.run(_drain_shadow_tasks(router))

    assert called["shadow"] == 1


def test_shadow_mode_survives_llm_failure() -> None:
    router = _build_router("shadow")

    async def _fake_rule(_intent: IntentResult, _context: SkillContext) -> SkillResult:
        return SkillResult(success=True, skill_name="QuerySkill", reply_text="rule")

    class _BrokenSelector:
        async def select(self, _message: str, _context: Any = None):
            raise RuntimeError("boom")

    router._rule_based_route = _fake_rule
    router._llm_selector = _BrokenSelector()

    result = asyncio.run(router.route(_build_intent(), _build_context()))

    asyncio.run(_drain_shadow_tasks(router))
    assert result.success is True
    assert result.skill_name == "QuerySkill"


def test_llm_mode_uses_llm_result() -> None:
    router = _build_router("llm")
    router._skills["QuerySkill"] = object()  # type: ignore[assignment]

    class _Selector:
        async def select(self, _message: str, _context: Any = None) -> LLMSelectionResult | None:
            return LLMSelectionResult(
                skill_name="query",
                confidence=0.9,
                reasoning="query intent",
                latency_ms=1.0,
            )

    async def _fake_execute(skill_name: str, _context: SkillContext) -> SkillResult:
        return SkillResult(success=True, skill_name=skill_name, reply_text="llm")

    async def _fake_rule(_intent: IntentResult, _context: SkillContext) -> SkillResult:
        return SkillResult(success=True, skill_name="ChitchatSkill", reply_text="rule")

    router._llm_selector = _Selector()
    router._execute_skill = _fake_execute
    router._rule_based_route = _fake_rule

    result = asyncio.run(router.route(_build_intent("ChitchatSkill"), _build_context("查一下案件")))

    assert result.skill_name == "QuerySkill"
    assert result.reply_text == "llm"


def test_llm_mode_falls_back_to_rule_on_failure() -> None:
    router = _build_router("llm")

    class _Selector:
        async def select(self, _message: str, _context: Any = None) -> LLMSelectionResult | None:
            return None

    async def _fake_rule(_intent: IntentResult, _context: SkillContext) -> SkillResult:
        return SkillResult(success=True, skill_name="SummarySkill", reply_text="rule")

    router._llm_selector = _Selector()
    router._rule_based_route = _fake_rule

    result = asyncio.run(router.route(_build_intent(), _build_context()))

    assert result.skill_name == "SummarySkill"
    assert result.reply_text == "rule"


def test_unknown_mode_falls_back_to_rule() -> None:
    router = _build_router("invalid")

    async def _fake_rule(_intent: IntentResult, _context: SkillContext) -> SkillResult:
        return SkillResult(success=True, skill_name="QuerySkill", reply_text="rule")

    router._rule_based_route = _fake_rule

    result = asyncio.run(router.route(_build_intent(), _build_context()))

    assert result.skill_name == "QuerySkill"
    assert result.reply_text == "rule"
