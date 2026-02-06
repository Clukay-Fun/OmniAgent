"""
描述: 飞书云文档搜索工具
主要功能:
    - 关键词搜索云文档 (Wiki, Doc, Sheet, Bitable)
    - 支持指定文件夹范围
"""

from __future__ import annotations

from typing import Any

from src.tools.base import BaseTool
from src.tools.registry import ToolRegistry


# region 云文档搜索
@ToolRegistry.register
class DocSearchTool(BaseTool):
    """
    云文档搜索工具

    功能:
        - 调用搜索 API 查找相关文档
        - 返回文档标题、链接及预览片段
    """
    name = "feishu.v1.doc.search"
    description = "Search Feishu documents by keyword."

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        执行搜索

        参数:
            params: 参数字典 (keyword, folder_token, limit)
        """
        keyword = params.get("keyword")
        if not keyword:
            return {"documents": []}

        settings = self.context.settings
        folder_token = params.get("folder_token") or settings.doc.search.default_folder_token
        limit = int(params.get("limit") or settings.doc.search.default_limit)
        payload: dict[str, Any] = {
            "search_key": keyword,
            "count": limit,
            "doc_types": ["doc", "docx", "sheet", "bitable"],
        }
        if folder_token:
            payload["folder_tokens"] = [folder_token]

        response = await self.context.client.request(
            "POST",
            "/suite/docs-api/search/object",
            json_body=payload,
        )
        data = response.get("data") or {}
        items = data.get("docs_entities") or []

        documents = []
        domain = settings.bitable.domain
        type_path = {
            "doc": "docs",
            "docx": "docx",
            "sheet": "sheets",
            "bitable": "base",
        }
        for item in items:
            doc_token = item.get("docs_token") or item.get("doc_token")
            doc_type = item.get("docs_type") or item.get("doc_type")
            title = item.get("title") or ""
            preview = item.get("preview") or ""
            if preview and len(preview) > settings.doc.search.preview_length:
                preview = preview[: settings.doc.search.preview_length]
            path = type_path.get(doc_type or "", "docs")
            url = f"https://{domain}.feishu.cn/{path}/{doc_token}" if doc_token else ""
            documents.append({
                "doc_token": doc_token,
                "title": title,
                "url": url,
                "preview": preview,
            })

        return {"documents": documents, "total": len(documents)}
# endregion
