"""
描述: 飞书文件管理工具集 (Phase 2 预留)
主要功能:
    - 文件上传/下载 (待实现)
    - 文件元数据获取 (待实现)
"""

from __future__ import annotations

from typing import Any

from src.tools.base import BaseTool
from src.tools.registry import ToolRegistry


# region 文件工具 (暂未实现)
@ToolRegistry.register
class FileUploadTool(BaseTool):
    """
    文件上传工具 (Phase 2)

    功能:
        - 上传本地文件到飞书云空间
    """
    name = "feishu.v1.file.upload"
    description = "Upload file to Feishu (Phase 2)."

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("feishu.v1.file.upload is not implemented")


@ToolRegistry.register
class FileDownloadTool(BaseTool):
    """
    文件下载工具 (Phase 2)

    功能:
        - 下载飞书云空间文件到本地
    """
    name = "feishu.v1.file.download"
    description = "Download file from Feishu (Phase 2)."

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("feishu.v1.file.download is not implemented")


@ToolRegistry.register
class FileMetaTool(BaseTool):
    """
    文件元数据工具 (Phase 2)
    
    功能:
        - 获取文件基础信息 (大小、类型、所有者)
    """
    name = "feishu.v1.file.meta.get"
    description = "Get file metadata from Feishu (Phase 2)."

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("feishu.v1.file.meta.get is not implemented")
# endregion
