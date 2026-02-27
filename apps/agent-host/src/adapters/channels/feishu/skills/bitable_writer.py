"""
描述: 该模块实现了Bitable的数据写入功能，包括创建、更新和删除记录。
主要功能:
    - 提供BitableWriter类，用于操作Bitable中的记录。
    - 支持创建、更新和删除记录，并生成记录的URL。
"""

from __future__ import annotations

from typing import Any

from src.adapters.channels.feishu.record_links import build_record_url
from src.core.skills.data_writer import DataWriter, MCPDataWriter, WriteResult


class BitableWriter(DataWriter):
    """
    BitableWriter类用于操作Bitable中的记录，支持创建、更新和删除记录。

    功能:
        - 初始化时接收一个MCP客户端实例。
        - 提供create方法用于创建记录。
        - 提供update方法用于更新记录。
        - 提供delete方法用于删除记录。
    """

    def __init__(self, mcp_client: Any) -> None:
        """
        初始化BitableWriter实例。

        功能:
            - 接收一个MCP客户端实例。
            - 初始化MCPDataWriter实例，配置相应的工具名称。
        """
        self._mcp = mcp_client
        self._writer = MCPDataWriter(
            mcp_client,
            create_tool_name="feishu.v1.bitable.record.create",
            update_tool_name="feishu.v1.bitable.record.update",
            delete_tool_name="feishu.v1.bitable.record.delete",
        )

    # region 数据操作方法
    async def create(
        self,
        table_id: str | None,
        fields: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
        """
        创建一条新的记录。

        功能:
            - 调用MCPDataWriter的create方法创建记录。
            - 构建并设置记录的URL。
        """
        result = await self._writer.create(
            table_id,
            fields,
            idempotency_key=idempotency_key,
        )
        result.record_url = build_record_url(table_id, result.record_id, result.record_url)
        return result

    async def update(
        self,
        table_id: str | None,
        record_id: str,
        fields: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
        """
        更新一条已有的记录。

        功能:
            - 调用MCPDataWriter的update方法更新记录。
            - 构建并设置记录的URL。
        """
        result = await self._writer.update(
            table_id,
            record_id,
            fields,
            idempotency_key=idempotency_key,
        )
        result.record_url = build_record_url(table_id, result.record_id or record_id, result.record_url)
        return result

    async def delete(
        self,
        table_id: str | None,
        record_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
        """
        删除一条记录。

        功能:
            - 调用MCPDataWriter的delete方法删除记录。
            - 构建并设置记录的URL。
        """
        _ = idempotency_key
        result = await self._writer.delete(table_id, record_id)
        result.record_url = build_record_url(table_id, result.record_id or record_id, result.record_url)
        return result
    # endregion
