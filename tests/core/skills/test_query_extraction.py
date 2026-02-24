from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.skills.query import QuerySkill  # noqa: E402
from src.core.skills.data_writer import WriteResult  # noqa: E402
from src.utils.time_parser import parse_time_range  # noqa: E402


class _NoopWriter:
    async def create(self, table_id, fields, *, idempotency_key=None):
        return WriteResult(success=True, record_id="rec_noop", fields=fields)

    async def update(self, table_id, record_id, fields, *, idempotency_key=None):
        return WriteResult(success=True, record_id=record_id, fields=fields)


def _build_skill(query_card_v2_enabled: bool = False) -> QuerySkill:
    settings = SimpleNamespace(reply=SimpleNamespace(query_card_v2_enabled=query_card_v2_enabled))
    return QuerySkill(mcp_client=object(), settings=settings, skills_config={}, data_writer=_NoopWriter())


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

    assert tool == "data.bitable.search_exact"
    assert params["field"] == "案号"
    assert params["value"] == "（2024）粤01民终28497号"


def test_build_params_enriches_search_person_with_entity_name() -> None:
    skill = _build_skill()
    tool, params = skill._build_bitable_params(
        "查看房怡康负责的案件",
        extra={"planner_plan": {"tool": "search_person", "params": {"field": "主办律师"}}},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_person"
    assert params["field"] == "主办律师"
    assert params["user_name"] == "房怡康"


def test_build_params_recent_hearing_defaults_date_window() -> None:
    skill = _build_skill()
    tool, params = skill._build_bitable_params(
        "查一下最近的开庭",
        extra={"planner_plan": {"tool": "search_date_range", "params": {"field": "开庭日"}}},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_date_range"
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
    assert skill._guess_date_field("这周有什么庭要开") == "开庭日"


def test_build_params_query_next_month_hearing_range() -> None:
    skill = _build_skill()
    tool, params = skill._build_bitable_params(
        "查询下个月的开庭安排",
        extra={"planner_plan": {"tool": "search_date_range", "params": {"field": "截止日"}}},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_date_range"
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

    assert tool == "data.bitable.search_date_range"
    assert params["field"] == "开庭日"
    assert params["date_from"] == params["date_to"]


def test_build_params_search_with_hearing_phrase_upgrades_to_date_range() -> None:
    skill = _build_skill()
    tool, params = skill._build_bitable_params(
        "这周有什么庭要开",
        extra={"planner_plan": {"tool": "search", "params": {}}},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_date_range"
    assert params["field"] == "开庭日"
    assert "date_from" in params and "date_to" in params


def test_is_case_domain_query_supports_hearing_phrase() -> None:
    skill = _build_skill()
    assert skill._is_case_domain_query("2月20号有什么庭要开") is True


def test_build_params_company_query_downgrades_person_exact_to_keyword() -> None:
    skill = _build_skill()
    tool, params = skill._build_bitable_params(
        "帮我查一下深圳市神州红国际软装艺术有限公司的案子",
        extra={
            "planner_plan": {
                "tool": "search_exact",
                "params": {"field": "主办律师", "value": "深圳市神州红国际软装艺术有限公司"},
            }
        },
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_keyword"
    assert params["keyword"] == "深圳市神州红国际软装艺术有限公司"


def test_empty_result_prefer_message_uses_message_text() -> None:
    skill = _build_skill()
    result = skill._empty_result("该时间范围内没有开庭安排", prefer_message=True)
    assert result.reply_text == "该时间范围内没有开庭安排"


def test_format_case_result_adds_query_navigation_pending_action_when_enabled() -> None:
    skill = _build_skill(query_card_v2_enabled=True)
    result = skill._format_case_result(
        records=[
            {"record_id": "rec_1", "record_url": "https://example.com/1", "fields_text": {"案号": "A-1"}},
            {"record_id": "rec_2", "record_url": "https://example.com/2", "fields_text": {"案号": "A-2"}},
        ],
        pagination={"has_more": True, "page_token": "pt_2", "current_page": 1, "total": 8},
        query_meta={"tool": "data.bitable.search", "params": {"table_id": "tbl_1"}},
    )

    pending = result.data.get("pending_action")
    assert isinstance(pending, dict)
    assert pending.get("action") == "query_list_navigation"
    callbacks = pending.get("payload", {}).get("callbacks", {})
    assert callbacks["query_list_next_page"]["kind"] == "pagination"
    assert callbacks["query_list_today_hearing"]["query"] == "今天开庭"


def test_build_params_structured_party_query_maps_to_target_fields() -> None:
    skill = _build_skill()
    tool, params = skill._build_bitable_params(
        "当事人是张三的案子",
        extra={},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_keyword"
    assert params["keyword"] == "张三"
    assert "委托人" in params["fields"]
    assert "对方当事人" in params["fields"]


def test_build_params_structured_court_query_maps_to_court_field() -> None:
    skill = _build_skill()
    tool, params = skill._build_bitable_params(
        "法院是广州中院的案件",
        extra={},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_keyword"
    assert params["keyword"] == "广州中院"
    assert params["fields"] == ["审理法院"]


def test_build_params_past_hearing_query_uses_date_to_before_today() -> None:
    skill = _build_skill()
    tool, params = skill._build_bitable_params(
        "已经开过庭的案子",
        extra={},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_date_range"
    assert params["field"] == "开庭日"
    assert params["date_to"] == (date.today() - timedelta(days=1)).isoformat()


def test_build_params_future_hearing_query_uses_date_from_today() -> None:
    skill = _build_skill()
    tool, params = skill._build_bitable_params(
        "后续要开庭的案子",
        extra={},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_date_range"
    assert params["field"] == "开庭日"
    assert params["date_from"] == date.today().isoformat()


def test_parse_time_range_supports_last_month() -> None:
    today = date.today()
    prev_month_end = today.replace(day=1) - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    parsed = parse_time_range("上个月开庭安排")

    assert parsed is not None
    assert parsed.date_from == prev_month_start.isoformat()
    assert parsed.date_to == prev_month_end.isoformat()


def test_parse_time_range_supports_after_two_days_phrase() -> None:
    parsed = parse_time_range("过两天开庭")
    assert parsed is not None
    target = date.today() + timedelta(days=2)
    assert parsed.date_from == target.isoformat()
    assert parsed.date_to == target.isoformat()


def test_parse_time_range_supports_future_n_days_phrase() -> None:
    parsed = parse_time_range("未来7天开庭安排")
    assert parsed is not None
    assert parsed.date_from == date.today().isoformat()
    assert parsed.date_to == (date.today() + timedelta(days=7)).isoformat()


def test_parse_time_range_supports_month_only_phrase() -> None:
    today = date.today()
    parsed = parse_time_range("2月开庭的案子")

    assert parsed is not None
    assert parsed.date_from == f"{today.year}-02-01"
    assert parsed.date_to.startswith(f"{today.year}-02-")
