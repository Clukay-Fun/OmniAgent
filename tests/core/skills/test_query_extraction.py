from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.skills.query import QuerySkill  # noqa: E402
import src.core.skills.query as query_module  # noqa: E402
from src.core.skills.data_writer import WriteResult  # noqa: E402
from src.core.skills.semantic_slots import SemanticSlotKey  # noqa: E402
from src.utils.time_parser import parse_time_range  # noqa: E402


class _NoopWriter:
    async def create(self, table_id, fields, *, idempotency_key=None):
        return WriteResult(success=True, record_id="rec_noop", fields=fields)

    async def update(self, table_id, record_id, fields, *, idempotency_key=None):
        return WriteResult(success=True, record_id=record_id, fields=fields)


def _build_skill(query_card_v2_enabled: bool = False) -> QuerySkill:
    settings = SimpleNamespace(reply=SimpleNamespace(query_card_v2_enabled=query_card_v2_enabled))
    return QuerySkill(mcp_client=object(), settings=settings, skills_config={}, data_writer=_NoopWriter())


def _build_params(skill: QuerySkill, query: str, extra: dict, table_result: dict) -> tuple[str, dict]:
    return asyncio.run(skill._build_bitable_params(query, extra=extra, table_result=table_result))


def test_select_target_contract_query_defaults_to_bitable() -> None:
    skill = _build_skill()

    assert skill._select_target("查合同") == "bitable"
    assert skill._select_target("查询合同台账") == "bitable"


def test_select_target_explicit_doc_commands_route_to_doc_search() -> None:
    skill = _build_skill()

    assert skill._select_target("查文档 合同模板") == "doc"
    assert skill._select_target("找文件 保密协议") == "doc"


def test_extract_entity_keyword_strips_action_noise() -> None:
    skill = _build_skill()
    assert skill._extract_entity_keyword("帮我查一下房怡康的案子") == "房怡康"
    assert skill._extract_entity_keyword("查看房怡康负责的案件") == "房怡康"


def test_extract_exact_field_cleans_case_number_tail() -> None:
    skill = _build_skill()
    exact = asyncio.run(skill._extract_exact_field("查询案号为（2024）粤01民终28497号的案件"))
    assert exact == {"field": "案号", "value": "（2024）粤01民终28497号"}


def test_extract_unlabeled_case_identifier() -> None:
    skill = _build_skill()
    value = skill._extract_unlabeled_case_identifier("查找JFTD-20260023")
    assert value == "JFTD-20260023"


def test_extract_semantic_slots_supports_case_identifier_and_party() -> None:
    skill = _build_skill()
    extraction = skill._extract_semantic_slots("查案号JFTD-20260023，当事人是张三")

    assert extraction.slots.get(SemanticSlotKey.CASE_IDENTIFIER) == "JFTD-20260023"
    assert extraction.slots.get(SemanticSlotKey.PARTY_A) == "张三"
    assert extraction.confidence is not None


def test_build_params_unlabeled_case_identifier_uses_id_field_keyword_search() -> None:
    skill = _build_skill()
    tool, params = _build_params(
        skill,
        "查找JFTD-20260023",
        extra={},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_keyword"
    assert params["keyword"] == "JFTD-20260023"
    assert "案号" in params["fields"]
    assert "项目ID" in params["fields"]


def test_build_params_semantic_case_identifier_has_higher_priority_than_party() -> None:
    skill = _build_skill()
    tool, params = _build_params(
        skill,
        "查找JFTD-20260023这条张三相关案件",
        extra={},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_keyword"
    assert params["keyword"] == "JFTD-20260023"
    assert skill._last_resolution_trace[0]["source"] == "semantic.case_identifier"


def test_build_params_semantic_party_compiles_party_fields() -> None:
    skill = _build_skill()
    tool, params = _build_params(
        skill,
        "请查当事人是张三的案件",
        extra={},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_keyword"
    assert params["keyword"] == "张三"
    assert "委托人" in params["fields"]
    assert "对方当事人" in params["fields"]
    assert skill._last_resolution_trace[0]["source"] == "semantic.party"


def test_build_params_semantic_disabled_falls_back_to_rule_and_keeps_trace() -> None:
    skill = QuerySkill(
        mcp_client=object(),
        settings=SimpleNamespace(reply=SimpleNamespace(query_card_v2_enabled=False)),
        skills_config={"query": {"semantic_resolution": {"enabled": False}}},
        data_writer=_NoopWriter(),
    )
    tool, params = _build_params(
        skill,
        "查找JFTD-20260023",
        extra={},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_keyword"
    assert params["keyword"] == "JFTD-20260023"
    assert skill._last_resolution_trace[0]["source"] == "semantic.skipped"
    assert skill._last_resolution_trace[0]["reason"] == "disabled"
    assert skill._last_resolution_trace[-1]["source"] == "rule.id_keyword"


def test_build_params_semantic_low_confidence_falls_back_to_rule() -> None:
    skill = QuerySkill(
        mcp_client=object(),
        settings=SimpleNamespace(reply=SimpleNamespace(query_card_v2_enabled=False)),
        skills_config={"query": {"semantic_resolution": {"enabled": True, "min_confidence": 0.9}}},
        data_writer=_NoopWriter(),
    )
    tool, params = _build_params(
        skill,
        "当事人是张三的案子",
        extra={},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_keyword"
    assert params["keyword"] == "张三"
    assert skill._last_resolution_trace[0]["source"] == "semantic.skipped"
    assert skill._last_resolution_trace[0]["reason"] == "low_confidence"
    assert skill._last_resolution_trace[-1]["source"] == "rule.structured_query"


def test_build_params_records_semantic_metrics(monkeypatch) -> None:
    resolution_events: list[tuple[str, str]] = []
    fallback_reasons: list[str] = []
    confidences: list[float] = []

    monkeypatch.setattr(
        query_module,
        "record_query_resolution",
        lambda source, status: resolution_events.append((source, status)),
    )
    monkeypatch.setattr(
        query_module,
        "record_query_semantic_fallback",
        lambda reason: fallback_reasons.append(reason),
    )
    monkeypatch.setattr(
        query_module,
        "observe_query_semantic_confidence",
        lambda value: confidences.append(float(value)),
    )

    skill = _build_skill()
    _build_params(skill, "查找JFTD-20260023", extra={}, table_result={"table_id": "tbl_x"})

    assert ("semantic.case_identifier", "selected") in resolution_events
    assert confidences

    disabled_skill = QuerySkill(
        mcp_client=object(),
        settings=SimpleNamespace(reply=SimpleNamespace(query_card_v2_enabled=False)),
        skills_config={"query": {"semantic_resolution": {"enabled": False}}},
        data_writer=_NoopWriter(),
    )
    _build_params(disabled_skill, "查找JFTD-20260023", extra={}, table_result={"table_id": "tbl_x"})
    assert "disabled" in fallback_reasons


def test_build_params_fills_planner_search_exact_value_from_query() -> None:
    skill = _build_skill()
    tool, params = _build_params(
        skill,
        "查询案号为（2024）粤01民终28497号的案子",
        extra={"planner_plan": {"tool": "search_exact", "params": {"field": "案号"}}},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_exact"
    assert params["field"] == "案号"
    assert params["value"] == "（2024）粤01民终28497号"


def test_build_params_enriches_search_person_with_entity_name() -> None:
    skill = _build_skill()
    tool, params = _build_params(
        skill,
        "查看房怡康负责的案件",
        extra={"planner_plan": {"tool": "search_person", "params": {"field": "主办律师"}}},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_person"
    assert params["field"] == "主办律师"
    assert params["user_name"] == "房怡康"


def test_build_params_recent_hearing_defaults_date_window() -> None:
    skill = _build_skill()
    tool, params = _build_params(
        skill,
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
    tool, params = _build_params(
        skill,
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
    tool, params = _build_params(
        skill,
        "2月20号有什么庭要开",
        extra={"planner_plan": {"tool": "search_date_range", "params": {}}},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_date_range"
    assert params["field"] == "开庭日"
    assert params["date_from"] == params["date_to"]


def test_build_params_search_with_hearing_phrase_upgrades_to_date_range() -> None:
    skill = _build_skill()
    tool, params = _build_params(
        skill,
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


def test_is_case_domain_query_supports_unlabeled_case_identifier() -> None:
    skill = _build_skill()
    assert skill._is_case_domain_query("查找JFTD-20260023") is True


def test_build_params_company_query_downgrades_person_exact_to_keyword() -> None:
    skill = _build_skill()
    tool, params = _build_params(
        skill,
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


def test_filter_records_for_org_entity_keeps_only_party_matches() -> None:
    skill = _build_skill()
    keyword = "小蝌蚪信息咨询（深圳）有限公司"
    records = [
        {
            "record_id": "rec_1",
            "fields_text": {
                "委托人": "深圳市中嘉建科股份有限公司",
                "备注": f"可能相关：{keyword}",
            },
        },
        {
            "record_id": "rec_2",
            "fields_text": {
                "委托人": keyword,
                "对方当事人": "某某公司",
            },
        },
    ]

    filtered = skill._filter_records_for_org_entity(records, keyword)
    assert [item.get("record_id") for item in filtered] == ["rec_2"]


def test_filter_records_for_org_entity_returns_empty_when_only_low_priority_hits() -> None:
    skill = _build_skill()
    keyword = "小蝌蚪信息咨询（深圳）有限公司"
    records = [
        {"record_id": "rec_1", "fields_text": {"备注": f"{keyword} 提到过"}},
        {"record_id": "rec_2", "fields_text": {"进展": f"相关方：{keyword}"}},
    ]

    filtered = skill._filter_records_for_org_entity(records, keyword)
    assert filtered == []


def test_empty_result_prefer_message_uses_message_text() -> None:
    skill = _build_skill()
    result = skill._empty_result("该时间范围内没有开庭安排", prefer_message=True)
    assert "该时间范围内没有开庭安排" in result.reply_text


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
    assert "当前仅展示前 2 条，还有 6 条未展示" in result.reply_text


def test_format_case_result_uses_markdown_list_and_status_badge() -> None:
    skill = _build_skill()
    result = skill._format_case_result(
        records=[
            {
                "record_id": "rec_1",
                "record_url": "https://example.com/1",
                "fields_text": {
                    "委托人及联系方式": "张三",
                    "对方当事人": "李四",
                    "案由": "合同纠纷",
                    "案号": "A-1",
                    "审理法院": "广州中院",
                    "程序阶段": "一审",
                    "案件状态": "进行中",
                },
            }
        ],
        pagination={"has_more": False, "page_token": "", "current_page": 1, "total": 1},
    )

    assert "- **1. 张三 vs 李四**｜合同纠纷" in result.reply_text
    assert "**状态**：🟡 进行中" in result.reply_text
    assert "[查看详情](https://example.com/1)" in result.reply_text


def test_build_params_structured_party_query_maps_to_target_fields() -> None:
    skill = _build_skill()
    tool, params = _build_params(
        skill,
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
    tool, params = _build_params(
        skill,
        "法院是广州中院的案件",
        extra={},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_keyword"
    assert params["keyword"] == "广州中院"
    assert params["fields"] == ["审理法院"]


def test_build_params_past_hearing_query_uses_date_to_before_today() -> None:
    skill = _build_skill()
    tool, params = _build_params(
        skill,
        "已经开过庭的案子",
        extra={},
        table_result={"table_id": "tbl_x"},
    )

    assert tool == "data.bitable.search_date_range"
    assert params["field"] == "开庭日"
    assert params["date_to"] == (date.today() - timedelta(days=1)).isoformat()


def test_build_params_future_hearing_query_uses_date_from_today() -> None:
    skill = _build_skill()
    tool, params = _build_params(
        skill,
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
