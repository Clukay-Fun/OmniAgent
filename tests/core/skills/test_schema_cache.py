from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.core.capabilities.skills.bitable.schema_cache import SchemaCache  # noqa: E402


class _FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_schema_cache_set_get_and_lookup_by_name_or_id() -> None:
    cache = SchemaCache()
    table_id = "tbl_cases"
    schema = [
        {"field_id": "fld_amount", "name": "金额", "type": 2, "type_name": "货币"},
        {"field_id": "fld_owner", "name": "负责人", "type": 11},
    ]

    cache.set_schema(table_id, schema)

    loaded = cache.get_schema(table_id)
    assert loaded is not None
    assert len(loaded) == 2
    assert cache.get_field_meta(table_id, "金额") == schema[0]
    assert cache.get_field_meta(table_id, "fld_owner") == schema[1]


def test_schema_cache_invalidate_and_safe_miss() -> None:
    cache = SchemaCache()
    cache.set_schema("tbl_x", [{"name": "案号", "type": 1}])
    cache.invalidate("tbl_x")

    assert cache.get_schema("tbl_x") is None
    assert cache.get_field_meta("tbl_x", "案号") is None
    assert cache.get_field_meta("", "案号") is None
    assert cache.get_schema("unknown") is None


def test_schema_cache_persists_file_backed_metadata(tmp_path) -> None:
    metadata_path = tmp_path / "schema_metadata.json"
    cache = SchemaCache(metadata_path=metadata_path)
    cache.set_schema("tbl_meta", [{"name": "案号", "type": 1}, {"name": "金额", "type": 2}])

    metadata = cache.get_metadata("tbl_meta")
    assert metadata is not None
    assert metadata["field_count"] == 2
    assert metadata.get("schema_hash")
    assert metadata_path.exists()


def test_schema_cache_loads_metadata_on_cold_start(tmp_path) -> None:
    metadata_path = tmp_path / "schema_metadata.json"
    first = SchemaCache(metadata_path=metadata_path)
    first.set_schema("tbl_meta", [{"name": "案号", "type": 1}])

    second = SchemaCache(metadata_path=metadata_path)
    loaded = second.get_metadata("tbl_meta")

    assert loaded is not None
    assert loaded["field_count"] == 1
    assert second.get_schema("tbl_meta") is None


def test_schema_cache_ttl_expiration_is_lazy_and_non_blocking() -> None:
    clock = _FakeClock(now=1000)
    cache = SchemaCache(ttl_seconds=600, clock=clock)
    cache.set_schema("tbl_ttl", [{"name": "案号", "type": 1}])

    assert cache.get_schema("tbl_ttl") is not None

    clock.advance(601)
    assert cache.get_schema("tbl_ttl") is None
    assert cache.get_field_meta("tbl_ttl", "案号") is None


def test_schema_cache_lru_evicts_oldest_table_when_over_cap() -> None:
    cache = SchemaCache(max_tables=20)
    for idx in range(20):
        cache.set_schema(f"tbl_{idx}", [{"name": f"字段{idx}", "type": 1}])

    cache.set_schema("tbl_20", [{"name": "字段20", "type": 1}])

    assert cache.get_schema("tbl_0") is None
    assert cache.get_schema("tbl_20") is not None


def test_schema_cache_invalidation_and_ttl_paths_coexist() -> None:
    clock = _FakeClock(now=2000)
    cache = SchemaCache(ttl_seconds=600, clock=clock)
    cache.set_schema("tbl_mix", [{"field_id": "f1", "name": "案号", "type": 1}])

    cache.invalidate("tbl_mix")
    assert cache.get_schema("tbl_mix") is None

    cache.set_schema("tbl_mix", [{"field_id": "f1", "name": "案号", "type": 1}])
    assert cache.get_field_meta("tbl_mix", "案号") is not None

    clock.advance(601)
    assert cache.get_schema("tbl_mix") is None


def test_schema_cache_manual_refresh_invalidate_entrypoint() -> None:
    cache = SchemaCache()
    cache.set_schema("tbl_refresh", [{"name": "金额", "type": 2}])

    cache.refresh("tbl_refresh")
    assert cache.get_schema("tbl_refresh") is None
