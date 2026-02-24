from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_preview_module(module_name: str) -> ModuleType:
    module_path = REPO_ROOT / "tools" / "dev" / "preview_card_payload.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init_midterm_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS midterm_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                value TEXT NOT NULL,
                source TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def test_extract_open_id_from_session_key() -> None:
    module = _load_preview_module("preview_card_payload_extract")

    assert module._extract_open_id_from_session_key("ou_abc123") == "ou_abc123"
    assert (
        module._extract_open_id_from_session_key("feishu:group:oc_1adf028:user:ou_group_user_9")
        == "ou_group_user_9"
    )
    assert module._extract_open_id_from_session_key("chat:oc_1adf028:anon") == ""


def test_find_recent_open_id_from_midterm_db(tmp_path: Path) -> None:
    module = _load_preview_module("preview_card_payload_midterm")
    db_path = tmp_path / "midterm_memory.sqlite3"
    _init_midterm_db(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO midterm_memory (user_id, kind, value, source, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("chat:oc_123:anon", "event", "skill:QuerySkill", "orchestrator", "{}", "2026-02-24T10:00:00Z"),
        )
        conn.execute(
            """
            INSERT INTO midterm_memory (user_id, kind, value, source, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "feishu:group:oc_1adf028:user:ou_recent_user_1",
                "event",
                "skill:QuerySkill",
                "orchestrator",
                "{}",
                "2026-02-24T10:01:00Z",
            ),
        )

    assert module._find_recent_open_id_from_midterm_db(db_path) == "ou_recent_user_1"


def test_extract_http_error_text_from_exception_response() -> None:
    module = _load_preview_module("preview_card_payload_http_error")

    class _Response:
        text = "bad request"

    class _Error(Exception):
        def __init__(self) -> None:
            super().__init__("boom")
            self.response = _Response()

    assert module._extract_http_error_text(_Error()) == "bad request"


def test_preset_spec_supports_operation_template_aliases() -> None:
    module = _load_preview_module("preview_card_payload_presets")

    template_id, version, params = module._preset_spec("c1-success")
    assert (template_id, version) == ("create.success", "v1")
    assert params["title"] == "新增成功"

    template_id, version, params = module._preset_spec("c2-confirm")
    assert (template_id, version) == ("action.confirm", "v1")
    assert params["action"] == "update_record"

    template_id, version, params = module._preset_spec("feedback")
    assert (template_id, version) == ("error.notice", "v1")
    assert params["error_class"] == "permission_denied"
