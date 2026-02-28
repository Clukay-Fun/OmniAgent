from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys


MCP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_ROOT))

from src.automation.schema import SchemaStateStore


def _build_cache_payload() -> dict[str, object]:
    return {
        "tables": {
            "app_1::tbl_1": {
                "app_token": "app_1",
                "table_id": "tbl_1",
                "fields_by_id": {
                    "fld_1": {
                        "name": "状态",
                        "type": 1,
                    }
                },
                "updated_at": "2026-02-20T00:00:00+00:00",
            }
        }
    }


def _build_runtime_payload() -> dict[str, object]:
    return {
        "disabled_rules": {
            "RULE_1": {
                "reason": "trigger_field_removed:状态",
                "at": "2026-02-20T00:00:00+00:00",
            }
        },
        "schema_tables": {
            "app_1::tbl_1": {
                "app_token": "app_1",
                "table_id": "tbl_1",
                "field_names": ["状态"],
                "field_types": {"状态": 1},
                "updated_at": "2026-02-20T00:00:00+00:00",
            }
        },
    }


def test_schema_state_store_round_trip_uses_sqlite_and_legacy_files(tmp_path: Path) -> None:
    cache_file = tmp_path / "schema_cache.json"
    runtime_file = tmp_path / "schema_runtime_state.json"
    db_file = tmp_path / "automation.db"
    store = SchemaStateStore(cache_file=cache_file, runtime_state_file=runtime_file, db_path=db_file)

    cache_payload = _build_cache_payload()
    runtime_payload = _build_runtime_payload()
    store.save_cache(cache_payload)
    store.save_runtime_state(runtime_payload)

    assert store.load_cache() == cache_payload
    assert store.load_runtime_state() == runtime_payload

    cache_json = json.loads(cache_file.read_text(encoding="utf-8"))
    runtime_json = json.loads(runtime_file.read_text(encoding="utf-8"))
    assert cache_json == cache_payload
    assert runtime_json == runtime_payload

    with sqlite3.connect(db_file) as conn:
        keys = sorted(row[0] for row in conn.execute("SELECT state_key FROM schema_state").fetchall())
        assert keys == ["cache", "runtime_state"]


def test_schema_state_store_migrates_legacy_json_to_sqlite(tmp_path: Path) -> None:
    cache_file = tmp_path / "schema_cache.json"
    runtime_file = tmp_path / "schema_runtime_state.json"
    db_file = tmp_path / "automation.db"

    cache_payload = _build_cache_payload()
    runtime_payload = _build_runtime_payload()
    cache_file.write_text(json.dumps(cache_payload, ensure_ascii=False), encoding="utf-8")
    runtime_file.write_text(json.dumps(runtime_payload, ensure_ascii=False), encoding="utf-8")

    store = SchemaStateStore(cache_file=cache_file, runtime_state_file=runtime_file, db_path=db_file)

    assert store.load_cache() == cache_payload
    assert store.load_runtime_state() == runtime_payload

    with sqlite3.connect(db_file) as conn:
        total = conn.execute("SELECT COUNT(1) FROM schema_state").fetchone()
        assert total is not None
        assert int(total[0]) == 2


def test_schema_state_store_does_not_override_existing_sqlite_state(tmp_path: Path) -> None:
    cache_file = tmp_path / "schema_cache.json"
    runtime_file = tmp_path / "schema_runtime_state.json"
    db_file = tmp_path / "automation.db"

    first_store = SchemaStateStore(cache_file=cache_file, runtime_state_file=runtime_file, db_path=db_file)
    first_payload = {
        "tables": {
            "app_1::tbl_1": {
                "table_id": "tbl_1",
            }
        }
    }
    first_store.save_cache(first_payload)

    legacy_override = {
        "tables": {
            "app_2::tbl_2": {
                "table_id": "tbl_2",
            }
        }
    }
    cache_file.write_text(json.dumps(legacy_override, ensure_ascii=False), encoding="utf-8")

    second_store = SchemaStateStore(cache_file=cache_file, runtime_state_file=runtime_file, db_path=db_file)
    assert second_store.load_cache() == first_payload
