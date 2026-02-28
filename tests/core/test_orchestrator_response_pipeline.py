import asyncio
from pathlib import Path
import sys
import types
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

# orchestrator imports Postgres client, which requires asyncpg at import time.
sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

from src.core.brain.orchestration.orchestrator import (
    AgentOrchestrator,
    _build_outbound_from_skill_result,
    _resolve_assistant_name,
)
import src.core.brain.orchestration.orchestrator as orchestrator_module
from src.core.foundation.common.types import SkillResult
from src.core.expression.response.models import RenderedResponse
from src.core.understanding.router.model_routing import RoutingDecision


def test_outbound_prefers_reply_text_as_text_fallback() -> None:
    result = SkillResult(
        success=True,
        skill_name="QuerySkill",
        message="来自 message",
        reply_text="来自 reply_text",
    )

    outbound = _build_outbound_from_skill_result(result)

    assert outbound["text_fallback"] == "来自 reply_text"


def test_reply_personalization_priority_explicit_over_session_and_default() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._reply_personalization_enabled = True

    class _StateManager:
        def __init__(self) -> None:
            self.saved: dict[str, str] = {"tone": "professional", "length": "short"}

        def set_reply_preferences(self, _user_id: str, preferences: dict[str, str]) -> None:
            self.saved.update(preferences)

        def get_reply_preferences(self, _user_id: str) -> dict[str, str]:
            return dict(self.saved)

    orchestrator._state_manager = _StateManager()

    rendered = RenderedResponse.from_outbound(
        {
            "text_fallback": "这是默认回复正文",
            "blocks": [{"type": "paragraph", "content": {"text": "这是默认回复正文"}}],
        },
        "这是默认回复正文",
    )

    updated = orchestrator._maybe_apply_reply_personalization(
        user_id="u1",
        user_text="请用口语风格，详细一点",
        rendered=rendered,
    )

    assert updated.text_fallback.startswith("好的，我来帮你整理如下")
    assert "如需我继续展开某一条" in updated.text_fallback


def test_reply_personalization_uses_session_memory_when_no_explicit_signal() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._reply_personalization_enabled = True
    orchestrator._state_manager = SimpleNamespace(
        set_reply_preferences=lambda *_args, **_kwargs: None,
        get_reply_preferences=lambda _user_id: {"tone": "friendly", "length": "medium"},
    )

    rendered = RenderedResponse.from_outbound(
        {
            "text_fallback": "查询完成",
            "blocks": [{"type": "paragraph", "content": {"text": "查询完成"}}],
        },
        "查询完成",
    )

    updated = orchestrator._maybe_apply_reply_personalization(user_id="u2", user_text="继续", rendered=rendered)

    assert updated.text_fallback.startswith("好的，我来帮你整理如下")


def test_outbound_contains_paragraph_block() -> None:
    result = SkillResult(success=True, skill_name="SummarySkill", message="汇总完成")

    outbound = _build_outbound_from_skill_result(result)

    paragraph_blocks = [block for block in outbound["blocks"] if block.get("type") == "paragraph"]
    assert len(paragraph_blocks) >= 1


def test_outbound_meta_contains_assistant_and_skill_name() -> None:
    result = SkillResult(success=True, skill_name="ReminderSkill", message="提醒创建成功")

    outbound = _build_outbound_from_skill_result(result, assistant_name="小测")

    assert outbound["meta"]["assistant_name"] == "小测"
    assert outbound["meta"]["skill_name"] == "ReminderSkill"


def test_resolve_assistant_name_reads_from_skills_config() -> None:
    assert _resolve_assistant_name({"assistant_name": "配置助手"}) == "配置助手"
    assert _resolve_assistant_name({}) == "小敬"


def test_clear_user_conversation_clears_session_context_and_state() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)

    calls: dict[str, str] = {}

    orchestrator._sessions = SimpleNamespace(
        clear_user=lambda user_id: calls.setdefault("session", user_id),
    )
    orchestrator._context_manager = SimpleNamespace(
        clear=lambda user_id: calls.setdefault("context", user_id),
    )
    orchestrator._state_manager = SimpleNamespace(
        clear_user=lambda user_id: calls.setdefault("state", user_id),
    )

    orchestrator.clear_user_conversation("discord_user_1")

    assert calls == {
        "session": "discord_user_1",
        "context": "discord_user_1",
        "state": "discord_user_1",
    }


def test_handle_message_always_returns_outbound_structure() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._assistant_name = "管道助手"
    orchestrator._settings = SimpleNamespace(
        reply=SimpleNamespace(templates=SimpleNamespace(error="错误：{message}"))
    )
    orchestrator._sessions = SimpleNamespace(
        cleanup_expired=lambda: None,
        add_message=lambda *_args, **_kwargs: None,
    )
    orchestrator._context_manager = SimpleNamespace(
        cleanup_expired=lambda: None,
        active_count=lambda: 0,
    )
    orchestrator._state_manager = SimpleNamespace(
        cleanup_expired=lambda: None,
        active_count=lambda: 0,
    )
    orchestrator._l0_engine = SimpleNamespace(
        evaluate=lambda *_args, **_kwargs: SimpleNamespace(
            handled=True,
            reply={"type": "text", "text": "来自L0"},
        )
    )

    reply = asyncio.run(orchestrator.handle_message(user_id="u1", text="hello"))

    outbound = reply.get("outbound")
    assert isinstance(outbound, dict)
    assert outbound["text_fallback"] == "来自L0"
    assert outbound["blocks"][0]["type"] == "paragraph"
    assert outbound["meta"]["assistant_name"] == "管道助手"


def test_handle_message_exception_path_also_contains_outbound() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._assistant_name = "异常助手"
    orchestrator._settings = SimpleNamespace(
        reply=SimpleNamespace(templates=SimpleNamespace(error="错误：{message}"))
    )
    orchestrator._sessions = SimpleNamespace(
        cleanup_expired=lambda: None,
        add_message=lambda *_args, **_kwargs: None,
    )
    orchestrator._context_manager = SimpleNamespace(
        cleanup_expired=lambda: None,
        active_count=lambda: 0,
    )
    orchestrator._state_manager = SimpleNamespace(
        cleanup_expired=lambda: None,
        active_count=lambda: 0,
    )
    orchestrator._l0_engine = SimpleNamespace(
        evaluate=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    reply = asyncio.run(orchestrator.handle_message(user_id="u2", text="hello"))

    assert "错误：处理出错" in reply["text"]
    assert reply["outbound"]["text_fallback"] == reply["text"]
    assert reply["outbound"]["meta"]["assistant_name"] == "异常助手"


def test_reload_config_refreshes_assistant_name_for_outbound(monkeypatch) -> None:
    class _DummyIntentParser:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class _DummySkillRouter:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class _DummyL0RuleEngine:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def evaluate(self, *_args, **_kwargs):
            return SimpleNamespace(handled=True, reply={"type": "text", "text": "reload-ok"})

    monkeypatch.setattr(orchestrator_module, "IntentParser", _DummyIntentParser)
    monkeypatch.setattr(orchestrator_module, "SkillRouter", _DummySkillRouter)
    monkeypatch.setattr(orchestrator_module, "L0RuleEngine", _DummyL0RuleEngine)

    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._assistant_name = "旧助手"
    orchestrator._llm = object()
    orchestrator._state_manager = SimpleNamespace(
        cleanup_expired=lambda: None,
        active_count=lambda: 0,
    )
    orchestrator._sessions = SimpleNamespace(
        cleanup_expired=lambda: None,
        add_message=lambda *_args, **_kwargs: None,
    )
    orchestrator._context_manager = SimpleNamespace(
        cleanup_expired=lambda: None,
        active_count=lambda: 0,
    )
    orchestrator._settings = SimpleNamespace(
        reply=SimpleNamespace(templates=SimpleNamespace(error="错误：{message}"))
    )
    orchestrator._register_skills = lambda: None
    orchestrator._load_l0_rules = lambda _path: {}
    orchestrator._load_skills_config = lambda _path: {
        "assistant_name": "新助手",
        "intent": {},
        "chain": {},
        "routing": {},
    }

    orchestrator.reload_config("config/skills.yaml")
    reply = asyncio.run(orchestrator.handle_message(user_id="u3", text="hello"))

    assert reply["outbound"]["meta"]["assistant_name"] == "新助手"


def test_build_llm_context_injects_midterm_memory_when_enabled() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._soul_manager = SimpleNamespace(build_system_prompt=lambda: "")
    orchestrator._memory_manager = SimpleNamespace(
        snapshot=lambda _user_id: SimpleNamespace(shared_memory="", user_memory="", recent_logs=""),
        search_memory=lambda *_args, **_kwargs: asyncio.sleep(0, result=""),
    )
    orchestrator._vector_top_k = 3
    orchestrator._midterm_memory_inject_to_llm = True
    orchestrator._midterm_memory_llm_recent_limit = 5
    orchestrator._midterm_memory_llm_max_chars = 40
    orchestrator._midterm_memory_store = SimpleNamespace(
        list_recent=lambda user_id, limit: [
            {"kind": "event", "value": "skill:QuerySkill", "metadata": {"skill_name": "QuerySkill"}},
            {"kind": "keyword", "value": "张三", "metadata": {}},
            {"kind": "keyword", "value": "合同", "metadata": {}},
        ]
    )

    context = asyncio.run(orchestrator._build_llm_context(user_id="u1", query="查一下"))

    assert "midterm_memory" in context
    assert "event:QuerySkill" in context["midterm_memory"]
    assert "keyword:张三" in context["midterm_memory"]
    assert len(context["midterm_memory"]) <= 43


def test_build_llm_context_skips_midterm_memory_when_disabled() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._soul_manager = SimpleNamespace(build_system_prompt=lambda: "")
    orchestrator._memory_manager = SimpleNamespace(
        snapshot=lambda _user_id: SimpleNamespace(shared_memory="", user_memory="", recent_logs=""),
        search_memory=lambda *_args, **_kwargs: asyncio.sleep(0, result=""),
    )
    orchestrator._vector_top_k = 3
    orchestrator._midterm_memory_inject_to_llm = False
    orchestrator._midterm_memory_store = SimpleNamespace(
        list_recent=lambda user_id, limit: [{"kind": "keyword", "value": "不会出现", "metadata": {}}]
    )

    context = asyncio.run(orchestrator._build_llm_context(user_id="u2", query="查一下"))

    assert context["midterm_memory"] == ""


def test_build_llm_context_truncates_file_context_by_budget() -> None:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._soul_manager = SimpleNamespace(build_system_prompt=lambda: "")
    orchestrator._memory_manager = SimpleNamespace(
        snapshot=lambda _user_id: SimpleNamespace(shared_memory="", user_memory="", recent_logs=""),
        search_memory=lambda *_args, **_kwargs: asyncio.sleep(0, result=""),
    )
    orchestrator._vector_top_k = 3
    orchestrator._midterm_memory_inject_to_llm = False
    orchestrator._midterm_memory_store = None
    orchestrator._file_context_enabled = True
    orchestrator._file_context_max_chars = 20
    orchestrator._file_context_max_tokens = 3

    context = asyncio.run(
        orchestrator._build_llm_context(
            user_id="u3",
            query="总结文件",
            file_markdown="ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        )
    )

    assert context["file_context"].endswith("...")
    assert len(context["file_context"]) <= 12


def test_record_usage_log_computes_cost_with_pricing_and_unknown_fallback() -> None:
    records: list[dict[str, Any]] = []

    class _DummyUsageLogger:
        def log(self, record):
            records.append(
                {
                    "model": record.model,
                    "cost": record.cost,
                    "estimated": record.estimated,
                    "metadata": record.metadata,
                    "business_metadata": getattr(record, "business_metadata", {}),
                }
            )
            return True

    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._usage_logger = _DummyUsageLogger()
    orchestrator._usage_model_pricing = {
        "priced-model": orchestrator_module.load_model_pricing(
            model_pricing_json='{"models":{"priced-model":{"input_per_1k":0.2,"output_per_1k":0.8}}}'
        )["priced-model"]
    }
    usage_payloads = [
        {
            "model": "priced-model",
            "token_count": 200,
            "prompt_tokens": 100,
            "completion_tokens": 100,
            "cost": 0.0,
            "estimated": True,
            "latency_ms": 10,
            "metadata": {},
        },
        {
            "model": "unknown-model",
            "token_count": 100,
            "prompt_tokens": 50,
            "completion_tokens": 50,
            "cost": 0.0,
            "estimated": True,
            "latency_ms": 10,
            "metadata": {},
        },
    ]
    orchestrator._drain_latest_llm_usage = lambda: usage_payloads.pop(0)
    orchestrator._llm = SimpleNamespace(model_name="fallback-model")

    route_decision = RoutingDecision(
        model_selected="priced-model",
        route_label="primary_default",
        complexity="medium",
        reason="default",
        in_ab_bucket=False,
        metadata={},
    )

    orchestrator._record_usage_log(
        "u1",
        "c1",
        "QuerySkill",
        "text",
        route_decision,
        business_metadata={"action_classification": "close_case", "close_semantic": "default"},
    )
    orchestrator._record_usage_log("u1", "c1", "QuerySkill", "text", route_decision)

    assert len(records) == 2
    assert records[0]["estimated"] is False
    assert float(records[0]["cost"]) > 0.0
    assert records[1]["estimated"] is True
    assert float(records[1]["cost"]) == 0.0
    assert records[1]["metadata"].get("cost_warning") == "unknown_model_pricing"
    assert records[0]["business_metadata"].get("close_semantic") == "default"
