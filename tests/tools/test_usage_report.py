from __future__ import annotations

import json
from pathlib import Path

from tools.usage_report import aggregate_usage, load_usage_records


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
        {"user_id": "u1", "skill": "QuerySkill", "model": "m1", "token_count": 70},
        {"user_id": "u2", "skill": "QuerySkill", "model": "m2", "token_count": 20},
        {"user_id": "u1", "skill": "SummarySkill", "model": "m1", "token_count": 30},
    ]

    data = aggregate_usage(records)

    assert data["total_records"] == 3
    assert data["total_tokens"] == 120
    assert data["by_user"]["u1"] == 100
    assert data["by_skill"]["QuerySkill"] == 90
    assert data["by_model"]["m1"] == 2
