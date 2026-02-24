from datetime import date, timedelta
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.feishu.template_runtime import FilterEngine, GroupEngine, SummaryEngine


def test_filter_engine_supports_compare_sort_limit() -> None:
    engine = FilterEngine()
    today = date.today()
    records = [
        {"urgency": "重要紧急", "hearing_date": (today + timedelta(days=3)).isoformat(), "project_id": "A-3"},
        {"urgency": "重要紧急", "hearing_date": (today + timedelta(days=1)).isoformat(), "project_id": "A-1"},
        {"urgency": "重要不紧急", "hearing_date": (today + timedelta(days=2)).isoformat(), "project_id": "A-2"},
    ]

    filtered = engine.execute(
        records,
        "urgency = 重要紧急, hearing_date >= today, sort: hearing_date asc, limit: 1",
        {},
    )

    assert len(filtered) == 1
    assert filtered[0]["project_id"] == "A-1"


def test_group_engine_supports_date_bucket_grouping() -> None:
    engine = GroupEngine()
    today = date.today()
    records = [
        {"hearing_date": (today - timedelta(days=1)).isoformat(), "project_id": "A-1"},
        {"hearing_date": today.isoformat(), "project_id": "A-2"},
        {"hearing_date": (today + timedelta(days=2)).isoformat(), "project_id": "A-3"},
    ]

    grouped = engine.execute(
        records,
        {
            "field": "hearing_date",
            "buckets": [
                {"label": "已过期", "condition": "< today"},
                {"label": "今日", "condition": "= today"},
                {"label": "本周", "condition": ">= today AND <= this_week_end"},
            ],
        },
    )

    assert grouped[0][0] == "已过期"
    assert grouped[0][1][0]["project_id"] == "A-1"
    assert grouped[1][0] == "今日"
    assert grouped[1][1][0]["project_id"] == "A-2"
    assert grouped[2][0] == "本周"
    assert grouped[2][1][0]["project_id"] == "A-3"


def test_summary_engine_supports_custom_variables() -> None:
    filter_engine = FilterEngine()
    summary_engine = SummaryEngine(filter_engine)
    today = date.today()
    records = [
        {"hearing_date": (today + timedelta(days=1)).isoformat(), "status": "未结"},
        {"hearing_date": (today - timedelta(days=1)).isoformat(), "status": "未结"},
    ]

    text = summary_engine.execute(
        records,
        {
            "template": "统计：总 {total}，待开 {upcoming}",
            "variables": {
                "upcoming": {"type": "count", "filter": "hearing_date >= today"},
            },
        },
    )

    assert "总 2" in text
    assert "待开 1" in text


def test_filter_engine_supports_in_range_operator() -> None:
    engine = FilterEngine()
    today = date.today()
    records = [
        {"hearing_date": (today + timedelta(days=1)).isoformat(), "project_id": "A-1"},
        {"hearing_date": (today + timedelta(days=5)).isoformat(), "project_id": "A-5"},
        {"hearing_date": (today + timedelta(days=10)).isoformat(), "project_id": "A-10"},
    ]

    result = engine.execute(
        records,
        f"hearing_date in_range {today.isoformat()} {(today + timedelta(days=7)).isoformat()}",
        {},
    )

    assert [item["project_id"] for item in result] == ["A-1", "A-5"]
