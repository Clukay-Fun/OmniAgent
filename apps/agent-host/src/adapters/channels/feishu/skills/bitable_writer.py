from __future__ import annotations

from typing import Any

from src.core.skills.data_writer import DataWriter, MCPDataWriter, WriteResult


class BitableWriter(DataWriter):
    def __init__(self, mcp_client: Any) -> None:
        self._writer = MCPDataWriter(
            mcp_client,
            create_tool_name="feishu.v1.bitable.record.create",
            update_tool_name="feishu.v1.bitable.record.update",
        )

    async def create(
        self,
        table_id: str | None,
        fields: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
        return await self._writer.create(
            table_id,
            fields,
            idempotency_key=idempotency_key,
        )

    async def update(
        self,
        table_id: str | None,
        record_id: str,
        fields: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
        return await self._writer.update(
            table_id,
            record_id,
            fields,
            idempotency_key=idempotency_key,
        )
