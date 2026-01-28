"""
File tools (Phase 2).
"""

from __future__ import annotations

from typing import Any

from src.tools.base import BaseTool
from src.tools.registry import ToolRegistry


@ToolRegistry.register
class FileUploadTool(BaseTool):
    name = "feishu.v1.file.upload"
    description = "Upload file to Feishu (Phase 2)."

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("feishu.v1.file.upload is not implemented")


@ToolRegistry.register
class FileDownloadTool(BaseTool):
    name = "feishu.v1.file.download"
    description = "Download file from Feishu (Phase 2)."

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("feishu.v1.file.download is not implemented")


@ToolRegistry.register
class FileMetaTool(BaseTool):
    name = "feishu.v1.file.meta.get"
    description = "Get file metadata from Feishu (Phase 2)."

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("feishu.v1.file.meta.get is not implemented")
