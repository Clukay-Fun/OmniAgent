from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.planner.engine import PlannerEngine  # noqa: E402
from src.core.planner.schema import PlannerOutput  # noqa: E402


def test_planner_output_close_semantic_invalid_fallbacks_to_default() -> None:
    output = PlannerOutput.model_validate(
        {
            "intent": "close_record",
            "tool": "record.close",
            "params": {"close_semantic": "unknown"},
            "confidence": 0.9,
            "clarify_question": "",
        }
    )
    assert output.params.get("close_semantic") == "default"


def test_planner_output_close_semantic_aliases_are_removed() -> None:
    output = PlannerOutput.model_validate(
        {
            "intent": "close_record",
            "tool": "record.close",
            "params": {"close_profile": "enforcement_end", "profile": "default"},
            "confidence": 0.9,
            "clarify_question": "",
        }
    )
    assert output.params.get("close_semantic") == "default"
    assert "close_profile" not in output.params
    assert "profile" not in output.params


def test_planner_fallback_detects_close_default() -> None:
    planner = PlannerEngine(llm_client=object(), scenarios_dir="/tmp/not-exists", enabled=False)
    output = planner._fallback_plan("这个案子结案了")
    assert output is not None
    assert output.intent == "close_record"
    assert output.tool == "record.close"
    assert output.params.get("close_semantic") == "default"


def test_planner_fallback_detects_close_enforcement_end() -> None:
    planner = PlannerEngine(llm_client=object(), scenarios_dir="/tmp/not-exists", enabled=False)
    output = planner._fallback_plan("终结本次执行")
    assert output is not None
    assert output.intent == "close_record"
    assert output.tool == "record.close"
    assert output.params.get("close_semantic") == "enforcement_end"


def test_planner_fallback_maps_structured_field_query() -> None:
    planner = PlannerEngine(llm_client=object(), scenarios_dir="/tmp/not-exists", enabled=False)
    output = planner._fallback_plan("法官是张三的案件")
    assert output is not None
    assert output.tool == "search_keyword"
    assert output.params.get("keyword") == "张三"
    assert output.params.get("fields") == ["承办法官"]


def test_planner_fallback_disambiguates_subject_for_current_user() -> None:
    planner = PlannerEngine(llm_client=object(), scenarios_dir="/tmp/not-exists", enabled=False)
    profile = SimpleNamespace(name="房怡康", lawyer_name="房怡康")
    output = planner._fallback_plan("房怡康的案子", user_profile=profile)
    assert output is not None
    assert output.intent == "query_my_cases"
    assert output.tool == "search_person"


def test_planner_fallback_disambiguates_subject_for_lawyer_like_name() -> None:
    planner = PlannerEngine(llm_client=object(), scenarios_dir="/tmp/not-exists", enabled=False)
    output = planner._fallback_plan("王磊的案子")
    assert output is not None
    assert output.tool == "search_keyword"
    assert output.params.get("fields") == ["主办律师", "协办律师"]


def test_planner_fallback_disambiguates_subject_for_org_name() -> None:
    planner = PlannerEngine(llm_client=object(), scenarios_dir="/tmp/not-exists", enabled=False)
    output = planner._fallback_plan("深圳市神州红国际软装艺术有限公司的案子")
    assert output is not None
    assert output.tool == "search_keyword"
    assert output.params.get("fields") == ["委托人", "对方当事人", "联系人"]
