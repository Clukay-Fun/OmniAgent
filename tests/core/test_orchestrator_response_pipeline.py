import asyncio
from pathlib import Path
import sys
import types
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
FEISHU_AGENT_ROOT = ROOT / "agent" / "feishu-agent"
sys.path.insert(0, str(FEISHU_AGENT_ROOT))

# orchestrator imports Postgres client, which requires asyncpg at import time.
sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

from src.core.orchestrator import (
    AgentOrchestrator,
    _build_outbound_from_skill_result,
    _resolve_assistant_name,
)
import src.core.orchestrator as orchestrator_module
from src.core.types import SkillResult


def test_outbound_prefers_reply_text_as_text_fallback() -> None:
    result = SkillResult(
        success=True,
        skill_name="QuerySkill",
        message="来自 message",
        reply_text="来自 reply_text",
    )

    outbound = _build_outbound_from_skill_result(result)

    assert outbound["text_fallback"] == "来自 reply_text"


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
