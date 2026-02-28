from __future__ import annotations

import asyncio
from pathlib import Path
import sys


MCP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_ROOT))

from src.config import BitableSettings, DocSearchSettings, DocSettings, Settings
from src.tools.base import ToolContext
from src.tools.doc import DocSearchTool


class _FakeClient:
    def __init__(self, response: dict) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    async def request(self, method: str, path: str, json_body: dict | None = None) -> dict:
        self.calls.append({"method": method, "path": path, "json_body": json_body or {}})
        return self._response


def _build_settings() -> Settings:
    return Settings(
        bitable=BitableSettings(domain="acme"),
        doc=DocSettings(search=DocSearchSettings(default_limit=10, preview_length=20)),
    )


def test_doc_search_requests_only_doc_docx_wiki() -> None:
    client = _FakeClient(response={"data": {"docs_entities": []}})
    tool = DocSearchTool(ToolContext(settings=_build_settings(), client=client))

    asyncio.run(tool.run({"keyword": "合同"}))

    assert len(client.calls) == 1
    request_payload = client.calls[0]["json_body"]
    assert isinstance(request_payload, dict)
    assert request_payload.get("doc_types") == ["doc", "docx", "wiki"]


def test_doc_search_filters_out_sheet_and_bitable_types() -> None:
    response = {
        "data": {
            "docs_entities": [
                {"docs_token": "doc_1", "docs_type": "doc", "title": "文档 A", "preview": "A"},
                {"docs_token": "docx_1", "docs_type": "docx", "title": "文档 B", "preview": "B"},
                {"docs_token": "wiki_1", "docs_type": "wiki", "title": "知识库", "preview": "C"},
                {"docs_token": "sheet_1", "docs_type": "sheet", "title": "表格", "preview": "D"},
                {"docs_token": "base_1", "docs_type": "bitable", "title": "多维表", "preview": "E"},
            ]
        }
    }
    client = _FakeClient(response=response)
    tool = DocSearchTool(ToolContext(settings=_build_settings(), client=client))

    result = asyncio.run(tool.run({"keyword": "合同"}))

    titles = [item.get("title") for item in result["documents"]]
    assert titles == ["文档 A", "文档 B", "知识库"]
    urls = [str(item.get("url") or "") for item in result["documents"]]
    assert all("/sheets/" not in url for url in urls)
    assert all("/base/" not in url for url in urls)


def test_doc_search_builds_wiki_path_url() -> None:
    response = {
        "data": {
            "docs_entities": [
                {"docs_token": "wiki_2", "docs_type": "wiki", "title": "Wiki 页面", "preview": "内容"}
            ]
        }
    }
    client = _FakeClient(response=response)
    tool = DocSearchTool(ToolContext(settings=_build_settings(), client=client))

    result = asyncio.run(tool.run({"keyword": "流程"}))

    assert result["documents"][0]["url"] == "https://acme.feishu.cn/wiki/wiki_2"
