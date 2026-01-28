from __future__ import annotations

import asyncio
from typing import Any

from src.config import Settings
from src.tools.base import ToolContext
from src.tools.bitable import BitableRecordGetTool, BitableSearchTool


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, path, json_body))
        if path.endswith("/fields"):
            return {
                "data": {
                    "items": [
                        {"field_name": "案号", "field_type": 1},
                        {"field_name": "开庭日", "field_type": 5},
                        {"field_name": "对方当 事人", "field_type": 1},
                    ]
                }
            }
        if path.endswith("/records/search"):
            return {
                "data": {
                    "items": [
                        {
                            "record_id": "rec1",
                            "fields": {
                                "案号": [{"text": "A"}],
                                "对方当 事人": [{"text": "B"}],
                            },
                        }
                    ],
                    "total": 1,
                }
            }
        if "/records/" in path:
            return {
                "data": {
                    "fields": {
                        "案号": [{"text": "A"}],
                        "对方当 事人": [{"text": "B"}],
                    }
                }
            }
        raise AssertionError("Unexpected request")


def _settings() -> Settings:
    settings = Settings()
    settings.bitable.default_app_token = "app"
    settings.bitable.default_table_id = "tbl"
    settings.bitable.search.searchable_fields = ["案号", "对方当事人"]
    settings.bitable.field_mapping["hearing_date"] = "开庭日"
    return settings


def test_bitable_search_builds_fields_text_and_filters() -> None:
    async def run() -> None:
        client = FakeClient()
        context = ToolContext(settings=_settings(), client=client)  # type: ignore[arg-type]
        tool = BitableSearchTool(context)
        result = await tool.run({"keyword": "李四", "date_from": "2026-01-01", "date_to": "2026-01-31"})

        assert result["total"] == 1
        record = result["records"][0]
        assert record["fields_text"]["案号"] == "A"
        assert record["fields_text"]["对方当事人"] == "B"

        search_call = [call for call in client.calls if call[1].endswith("/records/search")][0]
        payload = search_call[2] or {}
        assert payload["filter"]["conditions"][0]["field_name"] in ("案号", "对方当 事人")
        assert payload["filter"]["conditions"][1]["field_name"] == "开庭日"
        assert payload["filter"]["conditions"][1]["operator"] == "isGreaterEqual"

    asyncio.run(run())


def test_bitable_record_get_returns_text_fields() -> None:
    async def run() -> None:
        client = FakeClient()
        context = ToolContext(settings=_settings(), client=client)  # type: ignore[arg-type]
        tool = BitableRecordGetTool(context)
        result = await tool.run({"record_id": "rec1"})
        assert result["fields_text"]["案号"] == "A"
        assert result["fields_text"]["对方当事人"] == "B"

    asyncio.run(run())
