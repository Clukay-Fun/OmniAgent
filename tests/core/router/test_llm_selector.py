from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.router.llm_selector import LLMSkillSelector  # noqa: E402 # type: ignore[reportMissingImports]


class _FakeMetadataLoader:
    def __init__(self, skills: list[dict[str, str]] | None = None) -> None:
        self._skills = skills or []

    def get_all_for_routing(self) -> list[dict[str, str]]:
        return list(self._skills)


class _FakeLLM:
    def __init__(self, response: Any = None, delay_seconds: float = 0.0, exc: Exception | None = None) -> None:
        self._response = response
        self._delay_seconds = delay_seconds
        self._exc = exc

    async def chat_json(self, _prompt: str, timeout: float | None = None) -> Any:
        _ = timeout
        if self._delay_seconds > 0:
            await asyncio.sleep(self._delay_seconds)
        if self._exc is not None:
            raise self._exc
        return self._response


def _default_skills() -> list[dict[str, str]]:
    return [
        {
            "name": "query",
            "description": "query skill",
            "trigger_conditions": "用户要查询",
        },
        {
            "name": "chitchat",
            "description": "chitchat skill",
            "trigger_conditions": "用户闲聊",
        },
    ]


def test_select_returns_result_on_valid_json() -> None:
    selector = LLMSkillSelector(
        llm_client=_FakeLLM(
            response={"skill_name": "query", "confidence": 0.88, "reasoning": "查询意图明显"}
        ),
        metadata_loader=_FakeMetadataLoader(_default_skills()),
        confidence_threshold=0.6,
    )

    result = asyncio.run(selector.select("帮我查一下今天案件", context=[]))

    assert result is not None
    assert result.skill_name == "query"
    assert result.confidence == 0.88
    assert result.reasoning == "查询意图明显"
    assert result.latency_ms >= 0.0


def test_select_returns_none_on_low_confidence() -> None:
    selector = LLMSkillSelector(
        llm_client=_FakeLLM(
            response={"skill_name": "query", "confidence": 0.3, "reasoning": "不太确定"}
        ),
        metadata_loader=_FakeMetadataLoader(_default_skills()),
        confidence_threshold=0.6,
    )

    result = asyncio.run(selector.select("随便聊聊", context=[]))

    assert result is None


def test_select_returns_none_on_unknown_skill() -> None:
    selector = LLMSkillSelector(
        llm_client=_FakeLLM(
            response={"skill_name": "unknown_skill", "confidence": 0.95, "reasoning": "错误选择"}
        ),
        metadata_loader=_FakeMetadataLoader(_default_skills()),
        confidence_threshold=0.6,
    )

    result = asyncio.run(selector.select("帮我查一下", context=[]))

    assert result is None


def test_select_returns_none_on_invalid_json() -> None:
    selector = LLMSkillSelector(
        llm_client=_FakeLLM(response="this is not json"),
        metadata_loader=_FakeMetadataLoader(_default_skills()),
        confidence_threshold=0.6,
    )

    result = asyncio.run(selector.select("帮我查一下", context=[]))

    assert result is None


def test_select_returns_none_on_timeout() -> None:
    selector = LLMSkillSelector(
        llm_client=_FakeLLM(
            response={"skill_name": "query", "confidence": 0.9, "reasoning": "查询"},
            delay_seconds=0.2,
        ),
        metadata_loader=_FakeMetadataLoader(_default_skills()),
        timeout_seconds=0.1,
        confidence_threshold=0.6,
    )

    result = asyncio.run(selector.select("帮我查一下", context=[]))

    assert result is None


def test_select_parses_markdown_code_block() -> None:
    markdown_json = """```json
{"skill_name": "query", "confidence": 0.91, "reasoning": "命中查询"}
```"""
    selector = LLMSkillSelector(
        llm_client=_FakeLLM(response=markdown_json),
        metadata_loader=_FakeMetadataLoader(_default_skills()),
        confidence_threshold=0.6,
    )

    result = asyncio.run(selector.select("查一下今天案件", context=[]))

    assert result is not None
    assert result.skill_name == "query"
    assert result.confidence == 0.91


def test_select_returns_none_when_no_skills_loaded() -> None:
    selector = LLMSkillSelector(
        llm_client=_FakeLLM(
            response={"skill_name": "query", "confidence": 0.99, "reasoning": "无意义"}
        ),
        metadata_loader=_FakeMetadataLoader([]),
        confidence_threshold=0.6,
    )

    result = asyncio.run(selector.select("任何问题", context=[]))

    assert result is None
