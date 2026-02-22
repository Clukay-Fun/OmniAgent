from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.usage_logger import UsageLogger, UsageRecord


def test_usage_logger_writes_jsonl_with_date_template(tmp_path: Path) -> None:
    logger = UsageLogger(
        enabled=True,
        path_template=str(tmp_path / "usage-{date}.jsonl"),
        fail_open=True,
    )
    record = UsageRecord(
        ts="2026-02-22T12:00:00",
        user_id="u1",
        conversation_id="c1",
        model="gpt-4o-mini",
        skill="QuerySkill",
        token_count=123,
        cost=0.0,
        usage_source="text",
        estimated=True,
    )

    ok = logger.log(record)

    assert ok is True
    output = tmp_path / "usage-2026-02-22.jsonl"
    assert output.exists()
    line = output.read_text(encoding="utf-8").strip()
    assert '"user_id": "u1"' in line
    assert '"token_count": 123' in line


def test_usage_logger_fail_open_returns_false_on_error() -> None:
    logger = UsageLogger(
        enabled=True,
        path_template="/dev/null/usage-{date}.jsonl",
        fail_open=True,
    )
    record = UsageRecord(
        ts="2026-02-22T12:00:00",
        user_id="u1",
        conversation_id="c1",
        model="gpt-4o-mini",
        skill="QuerySkill",
        token_count=0,
        cost=0.0,
        usage_source="text",
        estimated=True,
    )

    ok = logger.log(record)

    assert ok is False


def test_usage_logger_fail_closed_raises_on_error() -> None:
    logger = UsageLogger(
        enabled=True,
        path_template="/dev/null/usage-{date}.jsonl",
        fail_open=False,
    )
    record = UsageRecord(
        ts="2026-02-22T12:00:00",
        user_id="u1",
        conversation_id="c1",
        model="gpt-4o-mini",
        skill="QuerySkill",
        token_count=0,
        cost=0.0,
        usage_source="text",
        estimated=True,
    )

    raised = False
    try:
        logger.log(record)
    except Exception:
        raised = True

    assert raised is True
