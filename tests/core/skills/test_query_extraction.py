from __future__ import annotations

from datetime import date
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.skills.query import QuerySkill  # noqa: E402
from src.utils.time_parser import parse_time_range  # noqa: E402


def _build_skill() -> QuerySkill:
    return QuerySkill(mcp_client=object(), skills_config={})


def test_extract_entity_keyword_strips_action_noise() -> None:
    skill = _build_skill()
    assert skill._extract_entity_keyword("帮我查一下房怡康的案子") == "房怡康"
    assert skill._extract_entity_keyword("查看房怡康负责的案件") == "房怡康"


def test_extract_exact_field_cleans_case_number_tail() -> None:
    skill = _build_skill()
    exact = skill._extract_exact_field("查询案号为（2024）粤01民终28497号的案件")
    assert exact == {"field": "案号", "value": "（2024）粤01民终28497号"}


def test_build_params_fills_planner_search_exact_value_from_query() -> None:
    skill = _build_skill()
    tool, params = skill._build_bitable_params(
        "查询案号为（2024）粤01民终28497号的案子",
        extra={"planner_plan": {"tool": "search_exact", "params": {"field": "案号"}}},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "feishu.v1.bitable.search_exact"
    assert params["field"] == "案号"
    assert params["value"] == "（2024）粤01民终28497号"


def test_build_params_enriches_search_person_with_entity_name() -> None:
    skill = _build_skill()
    tool, params = skill._build_bitable_params(
        "查看房怡康负责的案件",
        extra={"planner_plan": {"tool": "search_person", "params": {"field": "主办律师"}}},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "feishu.v1.bitable.search_person"
    assert params["field"] == "主办律师"
    assert params["user_name"] == "房怡康"


def test_build_params_recent_hearing_defaults_date_window() -> None:
    skill = _build_skill()
    tool, params = skill._build_bitable_params(
        "查一下最近的开庭",
        extra={"planner_plan": {"tool": "search_date_range", "params": {"field": "开庭日"}}},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "feishu.v1.bitable.search_date_range"
    assert "date_from" in params
    assert "date_to" in params
    assert date.fromisoformat(params["date_from"]) <= date.fromisoformat(params["date_to"])


def test_guess_date_field_supports_multiple_deadline_fields() -> None:
    skill = _build_skill()

    assert skill._guess_date_field("查询管辖权异议截止日") == "管辖权异议截止日"
    assert skill._guess_date_field("这周举证截止时间") == "举证截止日"
    assert skill._guess_date_field("查一下查封到期") == "查封到期日"
    assert skill._guess_date_field("本月上诉截止日") == "上诉截止日"
    assert skill._guess_date_field("明天上午有开庭吗") == "开庭日"


def test_build_params_query_next_month_hearing_range() -> None:
    skill = _build_skill()
    tool, params = skill._build_bitable_params(
        "查询下个月的开庭安排",
        extra={"planner_plan": {"tool": "search_date_range", "params": {"field": "截止日"}}},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "feishu.v1.bitable.search_date_range"
    assert params["field"] == "开庭日"
    assert "date_from" in params and "date_to" in params
    assert date.fromisoformat(params["date_from"]) <= date.fromisoformat(params["date_to"])


def test_parse_time_range_supports_next_month() -> None:
    parsed = parse_time_range("查询下个月的开庭安排")
    assert parsed is not None
    assert parsed.date_from.endswith("-01")
    assert parsed.date_from <= parsed.date_to


def test_build_params_explicit_date_hearing() -> None:
    skill = _build_skill()
    tool, params = skill._build_bitable_params(
        "2月20号有什么庭要开",
        extra={"planner_plan": {"tool": "search_date_range", "params": {}}},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "feishu.v1.bitable.search_date_range"
    assert params["field"] == "开庭日"
    assert params["date_from"] == params["date_to"]
