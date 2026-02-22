from __future__ import annotations

import json
from pathlib import Path

from tools.usage_report import aggregate_usage, load_usage_records, render_report


def test_load_usage_records_skips_malformed_and_filters_date(tmp_path: Path) -> None:
    file_path = tmp_path / "usage-2026-02-22.jsonl"
    rows = [
        {"ts": "2026-02-22T10:00:00", "user_id": "u1", "skill": "QuerySkill", "model": "m1", "token_count": 100},
        {"ts": "2026-02-21T10:00:00", "user_id": "u2", "skill": "SummarySkill", "model": "m1", "token_count": 50},
    ]
    lines = [json.dumps(rows[0], ensure_ascii=False), "{broken", json.dumps(rows[1], ensure_ascii=False)]
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    records = load_usage_records(file_path, "2026-02-22")

    assert len(records) == 1
    assert records[0]["user_id"] == "u1"


def test_aggregate_usage_groups_user_skill_and_model() -> None:
    records = [
        {
            "user_id": "u1",
            "skill": "QuerySkill",
            "model": "m1",
            "token_count": 70,
            "cost": 0.35,
            "usage_source": "text",
            "latency_ms": 100,
            "metadata": {"route_label": "ab_simple", "complexity": "simple"},
        },
        {
            "user_id": "u2",
            "skill": "QuerySkill",
            "model": "m2",
            "token_count": 20,
            "cost": 0.06,
            "usage_source": "file",
            "metadata": {"route_label": "primary_default", "complexity": "medium"},
        },
        {
            "user_id": "u1",
            "skill": "SummarySkill",
            "model": "m1",
            "token_count": 30,
            "cost": 0.12,
            "usage_source": "file",
            "latency_ms": 300,
            "metadata": {"route_label": "ab_complex", "complexity": "complex"},
        },
    ]

    data = aggregate_usage(records)

    assert data["total_records"] == 3
    assert data["total_tokens"] == 120
    assert round(float(data["total_cost"]), 2) == 0.53
    assert data["by_user"]["u1"] == 100
    assert round(float(data["by_user_cost"]["u1"]), 2) == 0.47
    assert data["by_skill"]["QuerySkill"] == 90
    assert round(float(data["by_skill_cost"]["QuerySkill"]), 2) == 0.41
    assert data["by_model"]["m1"]["calls"] == 2
    assert data["by_model"]["m1"]["tokens"] == 100
    assert round(float(data["by_model"]["m1"]["cost"]), 2) == 0.47
    assert data["by_model"]["m1"]["avg_latency_ms"] == 200
    assert data["by_source"]["file"]["calls"] == 2
    assert round(float(data["by_source"]["file"]["cost"]), 2) == 0.18
    assert data["by_route"]["ab_simple"] == 1
    assert data["by_complexity"]["complex"] == 1


def test_render_report_handles_missing_fields_robustly(tmp_path: Path) -> None:
    records = [{"user_id": "u1", "skill": "QuerySkill", "model": "m1"}]
    aggregated = aggregate_usage(records)

    text = render_report(aggregated, "2026-02-22", tmp_path / "usage.jsonl")

    assert "Model comparison:" in text
    assert "Total cost:" in text
    assert "Source distribution:" in text
    assert "Route distribution:" in text
