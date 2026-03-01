"""Agent 路由模式测试：验证 Tool Calling 路由的端到端行为"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.understanding.intent import IntentResult, SkillMatch  # noqa: E402
from src.core.understanding.router.router import SkillRouter  # noqa: E402
from src.core.understanding.router.agent_router import AgentRouter, _TOOL_TO_SKILL  # noqa: E402
from src.core.foundation.common.types import SkillContext, SkillResult  # noqa: E402


def _build_context(query: str = "测试消息") -> SkillContext:
    return SkillContext(query=query, user_id="u-test")


def _build_intent() -> IntentResult:
    return IntentResult(
        skills=[SkillMatch(name="AgentRouted", score=1.0, reason="agent_mode")],
        is_chain=False,
        requires_llm_confirm=False,
        method="agent",
    )


class _MockLLMClient:
    """模拟 LLM 客户端，返回指定的 tool call"""

    def __init__(self, tool_name: str = "query_records", tool_args: dict | None = None) -> None:
        self._tool_name = tool_name
        self._tool_args = tool_args or {"intent_summary": "test"}

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout: float | None = None,
        tool_choice: str | dict[str, Any] = "auto",
    ) -> dict[str, Any]:
        return {
            "content": None,
            "tool_calls": [
                {
                    "id": "call_test_001",
                    "name": self._tool_name,
                    "arguments": self._tool_args,
                }
            ],
        }


class _MockLLMClientNoToolCall:
    """模拟 LLM 客户端，不返回任何 tool call（视为闲聊）"""

    async def chat_with_tools(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "content": "你好！有什么可以帮你的？",
            "tool_calls": None,
        }


class _MockLLMClientError:
    """模拟 LLM 客户端，抛出异常"""

    async def chat_with_tools(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("LLM 连接失败")


# ─── AgentRouter 单元测试 ───


def test_agent_router_resolves_query_intent() -> None:
    """LLM 返回 query_records tool call → QuerySkill"""
    router = AgentRouter(llm_client=_MockLLMClient("query_records"), timeout_seconds=5.0)
    result = asyncio.run(router.resolve_intent("查一下所有案件"))
    assert result.method == "agent"
    top = result.top_skill()
    assert top is not None
    assert top.name == "QuerySkill"


def test_agent_router_resolves_create_intent() -> None:
    """LLM 返回 create_record tool call → CreateSkill"""
    router = AgentRouter(llm_client=_MockLLMClient("create_record"), timeout_seconds=5.0)
    result = asyncio.run(router.resolve_intent("新增一条案件"))
    assert result.top_skill().name == "CreateSkill"


def test_agent_router_resolves_update_intent() -> None:
    """LLM 返回 update_record tool call → UpdateSkill"""
    router = AgentRouter(llm_client=_MockLLMClient("update_record"), timeout_seconds=5.0)
    result = asyncio.run(router.resolve_intent("修改案件状态"))
    assert result.top_skill().name == "UpdateSkill"


def test_agent_router_resolves_delete_intent() -> None:
    """LLM 返回 delete_record tool call → DeleteSkill"""
    router = AgentRouter(llm_client=_MockLLMClient("delete_record"), timeout_seconds=5.0)
    result = asyncio.run(router.resolve_intent("删除这条记录"))
    assert result.top_skill().name == "DeleteSkill"


def test_agent_router_resolves_reminder_intent() -> None:
    """LLM 返回 create_reminder tool call → ReminderSkill"""
    router = AgentRouter(llm_client=_MockLLMClient("create_reminder"), timeout_seconds=5.0)
    result = asyncio.run(router.resolve_intent("提醒我明天开庭"))
    assert result.top_skill().name == "ReminderSkill"


def test_agent_router_resolves_summary_intent() -> None:
    """LLM 返回 summarize tool call → SummarySkill"""
    router = AgentRouter(llm_client=_MockLLMClient("summarize"), timeout_seconds=5.0)
    result = asyncio.run(router.resolve_intent("总结一下这些案件"))
    assert result.top_skill().name == "SummarySkill"


def test_agent_router_resolves_chitchat_intent() -> None:
    """LLM 返回 chitchat tool call → ChitchatSkill"""
    router = AgentRouter(llm_client=_MockLLMClient("chitchat"), timeout_seconds=5.0)
    result = asyncio.run(router.resolve_intent("你好"))
    assert result.top_skill().name == "ChitchatSkill"


def test_agent_router_no_tool_call_fallback_chitchat() -> None:
    """LLM 不调用任何工具 → ChitchatSkill"""
    router = AgentRouter(llm_client=_MockLLMClientNoToolCall(), timeout_seconds=5.0)
    result = asyncio.run(router.resolve_intent("随便聊聊"))
    assert result.top_skill().name == "ChitchatSkill"


def test_agent_router_error_fallback_chitchat() -> None:
    """LLM 调用异常 → ChitchatSkill"""
    router = AgentRouter(llm_client=_MockLLMClientError(), timeout_seconds=5.0)
    result = asyncio.run(router.resolve_intent("查一下案件"))
    assert result.top_skill().name == "ChitchatSkill"


def test_tool_to_skill_mapping_covers_all_schemas() -> None:
    """确保每个 tool schema 都有对应的 skill 映射"""
    from src.core.understanding.router.agent_router import SKILL_TOOL_SCHEMAS
    for schema in SKILL_TOOL_SCHEMAS:
        tool_name = schema["function"]["name"]
        assert tool_name in _TOOL_TO_SKILL, f"Tool {tool_name} 缺少 skill 映射"


# ─── SkillRouter agent 模式集成测试 ───


def test_skill_router_agent_mode_routes_correctly() -> None:
    """SkillRouter 在 agent 模式下通过 Tool Calling 路由到正确技能"""
    router = SkillRouter(
        skills_config={"routing": {"mode": "agent", "agent_timeout": 5.0}},
        llm_client=_MockLLMClient("create_record"),
    )

    class _FakeCreateSkill:
        name = "CreateSkill"
        async def execute(self, _context: SkillContext) -> SkillResult:
            return SkillResult(success=True, skill_name="CreateSkill", reply_text="created")

    class _FakeChitchatSkill:
        name = "ChitchatSkill"
        async def execute(self, _context: SkillContext) -> SkillResult:
            return SkillResult(success=True, skill_name="ChitchatSkill", reply_text="hi")

    router.register(_FakeCreateSkill())
    router.register(_FakeChitchatSkill())

    result = asyncio.run(router.route(_build_intent(), _build_context("新增案件")))
    assert result.success is True
    assert result.skill_name == "CreateSkill"


def test_skill_router_agent_mode_error_fallback() -> None:
    """Agent 模式下 LLM 调用失败 → 回退到 ChitchatSkill"""
    router = SkillRouter(
        skills_config={"routing": {"mode": "agent", "agent_timeout": 5.0}},
        llm_client=_MockLLMClientError(),
    )

    class _FakeChitchatSkill:
        name = "ChitchatSkill"
        async def execute(self, _context: SkillContext) -> SkillResult:
            return SkillResult(success=True, skill_name="ChitchatSkill", reply_text="fallback")

    router.register(_FakeChitchatSkill())

    result = asyncio.run(router.route(_build_intent(), _build_context("查案件")))
    assert result.success is True
    assert result.skill_name == "ChitchatSkill"
