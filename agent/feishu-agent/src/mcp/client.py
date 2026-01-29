"""
MCP client for Feishu Agent.

优化：
- 连接池复用
- 指标记录
- 自定义异常
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from src.config import Settings
from src.utils.exceptions import MCPConnectionError, MCPTimeoutError, MCPToolError
from src.utils.metrics import record_mcp_tool_call

logger = logging.getLogger(__name__)


# ============================================
# region MCPClient
# ============================================
class MCPClientError(RuntimeError):
    """MCP 客户端错误（向后兼容）"""
    
    def __init__(self, code: str, message: str, detail: object | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class MCPClient:
    """
    MCP 客户端
    
    特性：
    - HTTP 连接池复用
    - 自动重试
    - 指标记录
    """
    
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.mcp.base_url
        self._timeout = settings.mcp.request.timeout
        self._max_retries = settings.mcp.request.max_retries
        self._retry_delay = settings.mcp.request.retry_delay
        
        # 创建共享的 HTTP 客户端（连接池复用）
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                limits=httpx.Limits(
                    max_keepalive_connections=10,
                    max_connections=20,
                ),
            )
        return self._client

    async def close(self) -> None:
        """关闭客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def call_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        调用 MCP 工具
        
        Args:
            tool_name: 工具名称
            params: 工具参数
            
        Returns:
            工具执行结果
            
        Raises:
            MCPTimeoutError: 超时
            MCPToolError: 工具执行失败
            MCPConnectionError: 连接失败
        """
        url = f"{self._base_url}/mcp/tools/{tool_name}"
        status = "success"
        
        for attempt in range(self._max_retries + 1):
            try:
                client = await self._get_client()
                
                logger.debug(
                    f"Calling MCP tool",
                    extra={"tool": tool_name, "attempt": attempt},
                )
                
                response = await client.post(url, json={"params": params})
                response.raise_for_status()
                
                payload = response.json()
                if not payload.get("success"):
                    error = payload.get("error") or {}
                    status = "tool_error"
                    raise MCPToolError(
                        tool_name=tool_name,
                        cause=error.get("message", "Unknown error"),
                    )
                
                record_mcp_tool_call(tool_name, "success")
                return payload.get("data") or {}
                
            except httpx.TimeoutException as exc:
                status = "timeout"
                if attempt >= self._max_retries:
                    record_mcp_tool_call(tool_name, "timeout")
                    raise MCPTimeoutError(tool_name, self._timeout) from exc
                    
            except httpx.ConnectError as exc:
                status = "connection_error"
                if attempt >= self._max_retries:
                    record_mcp_tool_call(tool_name, "connection_error")
                    raise MCPConnectionError(url, str(exc)) from exc
                    
            except httpx.HTTPStatusError as exc:
                status = "http_error"
                if attempt >= self._max_retries:
                    record_mcp_tool_call(tool_name, "http_error")
                    raise MCPToolError(tool_name, f"HTTP {exc.response.status_code}") from exc
                    
            except MCPToolError:
                record_mcp_tool_call(tool_name, "tool_error")
                raise
                
            # 指数退避重试
            delay = self._retry_delay * (2 ** attempt)
            logger.warning(
                f"MCP call failed, retrying",
                extra={"tool": tool_name, "attempt": attempt, "delay": delay},
            )
            await asyncio.sleep(delay)
        
        # 不应到达这里
        record_mcp_tool_call(tool_name, "unknown_error")
        raise MCPToolError(tool_name, "Max retries exceeded")

    async def list_tools(self) -> list[dict[str, Any]]:
        """列出可用工具"""
        url = f"{self._base_url}/mcp/tools"
        client = await self._get_client()
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
        return data.get("tools") or []
# endregion
# ============================================
