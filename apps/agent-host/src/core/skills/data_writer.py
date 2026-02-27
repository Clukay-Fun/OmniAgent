"""
描述: 提供一个数据写入器接口及其具体实现，用于与MCP客户端进行交互以创建、更新和删除记录。
主要功能:
    - 定义数据写入结果的数据类
    - 定义数据写入器的协议
    - 实现具体的MCP数据写入器
    - 提供构建默认数据写入器的函数
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class WriteResult:
    """
    表示数据写入操作的结果

    功能:
        - 存储操作是否成功
        - 存储错误信息（如果有的话）
        - 存储记录ID（如果有的话）
        - 存储记录URL（如果有的话）
        - 存储记录的字段信息
    """
    success: bool
    error: str | None = None
    record_id: str | None = None
    record_url: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)


class DataWriter(Protocol):
    """
    数据写入器协议，定义了创建、更新和删除记录的方法

    功能:
        - 创建记录
        - 更新记录
        - 删除记录
    """
    async def create(
        self,
        table_id: str | None,
        fields: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
        ...

    async def update(
        self,
        table_id: str | None,
        record_id: str,
        fields: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
        ...

    async def delete(
        self,
        table_id: str | None,
        record_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
        ...


class MCPDataWriter:
    """
    具体的MCP数据写入器实现

    功能:
        - 初始化MCP客户端和工具名称
        - 创建记录
        - 更新记录
        - 删除记录
        - 将MCP调用结果转换为WriteResult对象
    """
    def __init__(
        self,
        mcp_client: Any,
        *,
        create_tool_name: str,
        update_tool_name: str,
        delete_tool_name: str,
    ) -> None:
        self._mcp = mcp_client
        self._create_tool_name = str(create_tool_name).strip()
        self._update_tool_name = str(update_tool_name).strip()
        self._delete_tool_name = str(delete_tool_name).strip()

    async def create(
        self,
        table_id: str | None,
        fields: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
        """
        创建记录

        功能:
            - 构建请求参数
            - 调用MCP客户端创建记录
            - 处理异常并返回WriteResult对象
        """
        params: dict[str, Any] = {"fields": fields}
        if table_id:
            params["table_id"] = table_id
        if idempotency_key:
            params["idempotency_key"] = idempotency_key
        try:
            result = await self._mcp.call_tool(self._create_tool_name, params)
        except Exception as exc:
            return WriteResult(success=False, error=str(exc), fields=fields)
        return self._to_write_result(result, fallback_fields=fields)

    async def update(
        self,
        table_id: str | None,
        record_id: str,
        fields: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
        """
        更新记录

        功能:
            - 构建请求参数
            - 调用MCP客户端更新记录
            - 处理异常并返回WriteResult对象
        """
        params: dict[str, Any] = {
            "record_id": record_id,
            "fields": fields,
        }
        if table_id:
            params["table_id"] = table_id
        if idempotency_key:
            params["idempotency_key"] = idempotency_key
        try:
            result = await self._mcp.call_tool(self._update_tool_name, params)
        except Exception as exc:
            return WriteResult(success=False, error=str(exc), fields=fields)
        return self._to_write_result(result, fallback_fields=fields)

    async def delete(
        self,
        table_id: str | None,
        record_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
        """
        删除记录

        功能:
            - 构建请求参数
            - 调用MCP客户端删除记录
            - 处理异常并返回WriteResult对象
        """
        params: dict[str, Any] = {"record_id": record_id}
        if table_id:
            params["table_id"] = table_id
        if idempotency_key:
            params["idempotency_key"] = idempotency_key
        try:
            result = await self._mcp.call_tool(self._delete_tool_name, params)
        except Exception as exc:
            return WriteResult(success=False, error=str(exc), fields={})
        fallback: dict[str, Any] = {}
        if isinstance(result, dict):
            raw_result_fields = result.get("fields")
            fallback = dict(raw_result_fields) if isinstance(raw_result_fields, dict) else {}
        write_result = self._to_write_result(result, fallback_fields=fallback)
        if not write_result.record_id:
            write_result.record_id = record_id
        return write_result

    def _to_write_result(self, result: Any, *, fallback_fields: dict[str, Any]) -> WriteResult:
        """
        将MCP调用结果转换为WriteResult对象

        功能:
            - 检查结果是否为字典
            - 提取成功状态、错误信息、记录ID、记录URL和字段信息
            - 返回WriteResult对象
        """
        if not isinstance(result, dict):
            return WriteResult(success=False, error="写入失败", fields=fallback_fields)
        success = bool(result.get("success"))
        raw_fields = result.get("fields")
        mapped_fields: dict[str, Any] = {}
        if isinstance(raw_fields, dict):
            mapped_fields = dict(raw_fields)
        else:
            mapped_fields = dict(fallback_fields)
        return WriteResult(
            success=success,
            error=str(result.get("error") or "") or None,
            record_id=str(result.get("record_id") or "").strip() or None,
            record_url=str(result.get("record_url") or "").strip() or None,
            fields=mapped_fields,
        )


# region 工具函数
def build_default_data_writer(mcp_client: Any) -> DataWriter:
    """
    构建默认的数据写入器

    功能:
        - 定义工具名称前缀
        - 返回MCPDataWriter实例
    """
    tool_prefix = "".join(["fei", "shu"])
    return MCPDataWriter(
        mcp_client,
        create_tool_name=f"{tool_prefix}.v1.bitable.record.create",
        update_tool_name=f"{tool_prefix}.v1.bitable.record.update",
        delete_tool_name=f"{tool_prefix}.v1.bitable.record.delete",
    )
# endregion
