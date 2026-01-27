"""
MCP HTTP API schemas.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class ToolError(BaseModel):
    code: str
    message: str
    detail: Any | None = None


class ToolResponse(BaseModel):
    success: bool
    data: dict[str, Any] | None = None
    error: ToolError | None = None
