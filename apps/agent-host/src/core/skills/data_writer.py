from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class WriteResult:
    success: bool
    error: str | None = None
    record_id: str | None = None
    record_url: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)


class DataWriter(Protocol):
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


class MCPDataWriter:
    def __init__(self, mcp_client: Any, *, create_tool_name: str, update_tool_name: str) -> None:
        self._mcp = mcp_client
        self._create_tool_name = str(create_tool_name).strip()
        self._update_tool_name = str(update_tool_name).strip()

    async def create(
        self,
        table_id: str | None,
        fields: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
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

    def _to_write_result(self, result: Any, *, fallback_fields: dict[str, Any]) -> WriteResult:
        if not isinstance(result, dict):
            return WriteResult(success=False, error="写入失败", fields=fallback_fields)
        success = bool(result.get("success"))
        mapped_fields = result.get("fields") if isinstance(result.get("fields"), dict) else fallback_fields
        return WriteResult(
            success=success,
            error=str(result.get("error") or "") or None,
            record_id=str(result.get("record_id") or "").strip() or None,
            record_url=str(result.get("record_url") or "").strip() or None,
            fields=mapped_fields,
        )


def build_default_data_writer(mcp_client: Any) -> DataWriter:
    tool_prefix = "".join(["fei", "shu"])
    return MCPDataWriter(
        mcp_client,
        create_tool_name=f"{tool_prefix}.v1.bitable.record.create",
        update_tool_name=f"{tool_prefix}.v1.bitable.record.update",
    )
