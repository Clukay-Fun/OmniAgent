"""
MCP client for Feishu Agent.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from src.config import Settings


class MCPClientError(RuntimeError):
    def __init__(self, code: str, message: str, detail: object | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class MCPClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def call_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._settings.mcp.base_url}/mcp/tools/{tool_name}"
        retries = self._settings.mcp.request.max_retries
        delay = self._settings.mcp.request.retry_delay
        timeout = self._settings.mcp.request.timeout

        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(url, json={"params": params})
                response.raise_for_status()
                payload = response.json()
                if not payload.get("success"):
                    error = payload.get("error") or {}
                    raise MCPClientError(
                        code=error.get("code") or "MCP_ERROR",
                        message=error.get("message") or "MCP tool error",
                        detail=error.get("detail"),
                    )
                return payload.get("data") or {}
            except httpx.TimeoutException as exc:
                if attempt >= retries:
                    raise MCPClientError("TIMEOUT", "MCP request timed out") from exc
            except httpx.HTTPError as exc:
                if attempt >= retries:
                    raise MCPClientError("HTTP_ERROR", str(exc)) from exc
            except MCPClientError:
                if attempt >= retries:
                    raise
            await asyncio.sleep(delay * (2 ** attempt))

        raise MCPClientError("MCP_ERROR", "MCP tool request failed")

    async def list_tools(self) -> list[dict[str, Any]]:
        url = f"{self._settings.mcp.base_url}/mcp/tools"
        async with httpx.AsyncClient(timeout=self._settings.mcp.request.timeout) as client:
            response = await client.get(url)
        response.raise_for_status()
        data = response.json()
        return data.get("tools") or []
