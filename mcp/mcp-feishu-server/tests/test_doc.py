from __future__ import annotations

import asyncio
from typing import Any

from src.config import Settings
from src.tools.base import ToolContext
from src.tools.doc import DocSearchTool


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
        return {
            "data": {
                "docs_entities": [
                    {
                        "docs_token": "doc123",
                        "docs_type": "doc",
                        "title": "Test",
                        "preview": "x" * 500,
                    }
                ]
            }
        }


def test_doc_search_returns_preview_and_url() -> None:
    async def run() -> None:
        settings = Settings()
        settings.bitable.domain = "my"
        settings.doc.search.preview_length = 50
        client = FakeClient()
        context = ToolContext(settings=settings, client=client)  # type: ignore[arg-type]
        tool = DocSearchTool(context)
        result = await tool.run({"keyword": "合同", "limit": 1})
        doc = result["documents"][0]
        assert doc["doc_token"] == "doc123"
        assert doc["url"].startswith("https://my.feishu.cn/docs/")
        assert len(doc["preview"]) == 50

    asyncio.run(run())
