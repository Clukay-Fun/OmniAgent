from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys


MCP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_ROOT))

from src.automation.deadletter import DeadLetterStore
from src.automation.runlog import RunLogStore


def test_runlog_store_writes_sqlite_only(tmp_path: Path) -> None:
    legacy_file = tmp_path / "run_logs.jsonl"
    db_file = tmp_path / "automation.db"
    store = RunLogStore(db_path=db_file)

    store.write({"event_id": "evt_1", "result": "success"})

    assert not legacy_file.exists()
    with sqlite3.connect(db_file) as conn:
        row = conn.execute("SELECT payload_json FROM run_logs LIMIT 1").fetchone()
    assert row is not None
    payload = json.loads(str(row[0]))
    assert payload["event_id"] == "evt_1"
    assert payload["result"] == "success"


def test_deadletter_store_writes_sqlite_only(tmp_path: Path) -> None:
    legacy_file = tmp_path / "dead_letters.jsonl"
    db_file = tmp_path / "automation.db"
    store = DeadLetterStore(db_path=db_file)

    store.write({"rule_id": "r1", "error": "boom"})

    assert not legacy_file.exists()
    with sqlite3.connect(db_file) as conn:
        row = conn.execute("SELECT payload_json FROM dead_letters LIMIT 1").fetchone()
    assert row is not None
    payload = json.loads(str(row[0]))
    assert payload["rule_id"] == "r1"
    assert payload["error"] == "boom"


def test_runlog_store_does_not_import_legacy_jsonl(tmp_path: Path) -> None:
    legacy_file = tmp_path / "run_logs.jsonl"
    legacy_file.write_text(
        json.dumps({"timestamp": "2026-02-20T00:00:00+00:00", "event_id": "evt_legacy"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    db_file = tmp_path / "automation.db"

    store = RunLogStore(db_path=db_file)
    store.write({"event_id": "evt_new", "result": "success"})

    with sqlite3.connect(db_file) as conn:
        total_row = conn.execute("SELECT COUNT(1) FROM run_logs").fetchone()
    assert total_row is not None
    assert int(total_row[0]) == 1


def test_deadletter_store_does_not_import_legacy_jsonl(tmp_path: Path) -> None:
    legacy_file = tmp_path / "dead_letters.jsonl"
    legacy_file.write_text(
        json.dumps({"timestamp": "2026-02-20T00:00:00+00:00", "rule_id": "r_legacy"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    db_file = tmp_path / "automation.db"

    store = DeadLetterStore(db_path=db_file)
    store.write({"rule_id": "r_new", "error": "oops"})

    with sqlite3.connect(db_file) as conn:
        total_row = conn.execute("SELECT COUNT(1) FROM dead_letters").fetchone()
    assert total_row is not None
    assert int(total_row[0]) == 1
