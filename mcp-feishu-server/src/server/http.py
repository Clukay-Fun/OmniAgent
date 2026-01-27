"""
HTTP API for MCP tools.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from src.config import get_settings
from src.feishu.client import FeishuClient, FeishuAPIError
from src.server.schema import ToolRequest, ToolResponse, ToolError
from src.tools.base import ToolContext
from src.tools.registry import ToolRegistry


router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/mcp/tools")
async def list_tools() -> dict[str, Any]:
    settings = get_settings()
    tools = ToolRegistry.list_tools()
    if settings.tools.enabled:
        tools = [tool for tool in tools if tool["name"] in settings.tools.enabled]
    return {"tools": tools}


@router.post("/mcp/tools/{tool_name}", response_model=ToolResponse)
async def call_tool(tool_name: str, request: ToolRequest) -> ToolResponse:
    tool_cls = ToolRegistry.get(tool_name)
    if not tool_cls:
        raise HTTPException(status_code=404, detail="Tool not found")

    settings = get_settings()
    if settings.tools.enabled and tool_name not in settings.tools.enabled:
        raise HTTPException(status_code=403, detail="Tool disabled")
    context = ToolContext(settings=settings, client=FeishuClient(settings))
    tool = tool_cls(context)

    try:
        data = await tool.run(request.params)
        return ToolResponse(success=True, data=data)
    except FeishuAPIError as exc:
        return ToolResponse(
            success=False,
            error=ToolError(code="MCP_001", message=str(exc), detail=exc.detail),
        )
    except NotImplementedError as exc:
        return ToolResponse(
            success=False,
            error=ToolError(code="MCP_004", message=str(exc), detail=None),
        )
    except Exception as exc:
        return ToolResponse(
            success=False,
            error=ToolError(code="MCP_001", message=str(exc), detail=None),
        )
