"""
描述: MCP Server HTTP 接口层
主要功能:
    - 提供 MCP 工具列表查询
    - 处理工具调用请求
    - 辅助调试接口
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response

from src.config import get_settings
from src.feishu.client import FeishuClient, FeishuAPIError
from src.server.schema import ToolRequest, ToolResponse, ToolError
from src.tools.base import ToolContext
from src.tools.registry import ToolRegistry


router = APIRouter()


# region 基础路由
@router.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok", "service": "mcp-feishu-server"}


@router.get("/favicon.ico")
async def favicon() -> Response:
    return Response(status_code=204)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# endregion


# region MCP 工具接口
@router.get("/mcp/tools")
async def list_tools() -> dict[str, Any]:
    """获取可用工具列表"""
    settings = get_settings()
    tools = ToolRegistry.list_tools()
    if settings.tools.enabled:
        tools = [tool for tool in tools if tool["name"] in settings.tools.enabled]
    return {"tools": tools}


@router.post("/mcp/tools/{tool_name}", response_model=ToolResponse)
async def call_tool(tool_name: str, request: ToolRequest) -> ToolResponse:
    """
    调用 MCP 工具

    参数:
        tool_name: 工具名称
        request: 调用请求参数

    返回:
        工具执行结果
    """
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


# endregion


# region 调试接口
@router.get("/bitable/fields")
async def get_bitable_fields() -> dict[str, Any]:
    """
    获取多维表格字段元数据

    功能:
        - 读取当前配置的 Table 所有字段
        - 用于辅助配置字段映射关系

    返回:
        字段列表及元数据
    """
    settings = get_settings()
    client = FeishuClient(settings)
    
    app_token = settings.bitable.default_app_token
    table_id = settings.bitable.default_table_id
    
    if not app_token or not table_id:
        raise HTTPException(status_code=400, detail="Bitable not configured")
    
    try:
        response = await client.request(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
        )
        data = response.get("data") or {}
        items = data.get("items") or []
        
        fields = []
        for item in items:
            fields.append({
                "name": item.get("field_name"),
                "type": item.get("type"),
                "type_name": _get_field_type_name(item.get("type")),
            })
        
        return {
            "app_token": app_token,
            "table_id": table_id,
            "fields": fields,
            "total": len(fields),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def _get_field_type_name(field_type: int | None) -> str:
    """多维表格字段类型映射"""
    type_map = {
        1: "文本",
        2: "数字",
        3: "单选",
        4: "多选",
        5: "日期",
        7: "复选框",
        11: "人员",
        13: "电话",
        15: "超链接",
        17: "附件",
        18: "单向关联",
        19: "公式",
        20: "双向关联",
        21: "地理位置",
        22: "群组",
        23: "创建时间",
        1001: "创建人",
        1002: "修改人",
        1003: "修改时间",
    }
# endregion
