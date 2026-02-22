from __future__ import annotations

from typing import Any

from src.adapters.channels.feishu.record_links import build_record_url
from src.core.skills.data_writer import DataWriter, MCPDataWriter, WriteResult


class BitableWriter(DataWriter):
    def __init__(self, mcp_client: Any) -> None:
        self._mcp = mcp_client
        self._writer = MCPDataWriter(
            mcp_client,
            create_tool_name="feishu.v1.bitable.record.create",
            update_tool_name="feishu.v1.bitable.record.update",
            delete_tool_name="feishu.v1.bitable.record.delete",
        )

    async def create(
        self,
        table_id: str | None,
        fields: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
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
        _ = idempotency_key
        result = await self._writer.delete(table_id, record_id)
        result.record_url = build_record_url(table_id, result.record_id or record_id, result.record_url)
        return result
