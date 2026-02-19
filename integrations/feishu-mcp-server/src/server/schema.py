"""
描述: MCP Server HTTP API 数据模型
主要功能:
    - 定义工具调用请求 (ToolRequest)
    - 定义标准响应格式 (ToolResponse)
    - 定义错误结构 (ToolError)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# region API 数据模型
class ToolRequest(BaseModel):
    """工具调用请求体"""
    params: dict[str, Any] = Field(default_factory=dict)


class ToolError(BaseModel):
    """工具错误信息"""
    code: str
    message: str
    detail: Any | None = None


class ToolResponse(BaseModel):
    """工具调用响应"""
    success: bool
    data: dict[str, Any] | None = None
    error: ToolError | None = None
# endregion
