"""
描述: Feishu Agent MCP 客户端
主要功能:
    - 代理调用 MCP Server 工具接口
    - 维护 HTTP 连接池 (Connection Pooling)
    - 自动重试与指标上报
    - 统一异常封装
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx

from src.config import Settings
from src.utils.errors.exceptions import MCPConnectionError, MCPTimeoutError, MCPToolError
from src.utils.observability.metrics import record_mcp_tool_call

logger = logging.getLogger(__name__)


def _resolve_tool_alias(tool_name: str) -> str:
    normalized = str(tool_name or "").strip()
    if normalized.startswith("data.bitable."):
        return "feishu.v1.bitable." + normalized[len("data.bitable.") :]
    return normalized


_MCP_CONTAINER_HOST_ALIASES: set[str] = {
    "mcp-feishu-server",
    "feishu-mcp-server",
    "mcp",
}


def _is_running_in_container() -> bool:
    return os.path.exists("/.dockerenv")


def _with_host(parts: SplitResult, host: str) -> str:
    port_suffix = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme, f"{host}{port_suffix}", parts.path, parts.query, parts.fragment)).rstrip("/")


def _build_base_url_candidates(base_url: str) -> list[str]:
    normalized = str(base_url or "").strip().rstrip("/")
    if not normalized:
        return []

    parts = urlsplit(normalized)
    if not parts.scheme or not parts.netloc:
        return [normalized]

    host = (parts.hostname or "").lower()
    in_container = _is_running_in_container()
    candidates: list[str] = [normalized]

    if not in_container and host in _MCP_CONTAINER_HOST_ALIASES:
        candidates.append(_with_host(parts, "localhost"))
        candidates.append(_with_host(parts, "127.0.0.1"))
    elif in_container and host in {"localhost", "127.0.0.1"}:
        candidates.append(_with_host(parts, "mcp-feishu-server"))

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


# region 客户端与异常模型
class MCPClientError(RuntimeError):
    """MCP 客户端错误基类"""

    def __init__(self, code: str, message: str, detail: object | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class MCPClient:
    """
    MCP 客户端

    功能:
        - 封装 MCP 协议调用逻辑
        - 复用 AsyncClient 以提升并发性能
        - 实现指数退避重试 (Exponential Backoff)
        - 收集吞吐量与耗时指标
    """
    
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_urls = _build_base_url_candidates(settings.mcp.base_url)
        if not self._base_urls:
            self._base_urls = [str(settings.mcp.base_url or "").strip().rstrip("/")]
        self._timeout = settings.mcp.request.timeout
        self._max_retries = settings.mcp.request.max_retries
        self._retry_delay = settings.mcp.request.retry_delay
        
        # 创建共享的 HTTP 客户端（连接池复用）
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端 (实现单例模式)"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                trust_env=False,
                limits=httpx.Limits(
                    max_keepalive_connections=5,
                    max_connections=20,
                    keepalive_expiry=4.0,  # Uvicorn 默认 5s，提前 1s 主动过期防止复用死连接
                ),
            )
        return self._client

    async def close(self) -> None:
        """关闭客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _reset_client(self) -> None:
        """重置底层 HTTP 客户端，清理潜在坏连接"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def call_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        调用 MCP 工具

        参数:
            tool_name: 工具名称
            params: 参数字典

        返回:
            工具执行结果 (Data 字段)

        抛出:
            MCPTimeoutError: 请求超时
            MCPToolError: 工具返回错误或 HTTP 状态异常
            MCPConnectionError: 网络连接失败
        """
        normalized_tool_name = _resolve_tool_alias(tool_name)
        status = "success"
        last_url = ""
        last_error: Exception | None = None
        
        for attempt in range(self._max_retries + 1):
            for base_url in self._base_urls:
                url = f"{base_url}/mcp/tools/{normalized_tool_name}"
                last_url = url
                try:
                    client = await self._get_client()

                    logger.debug(
                        "调用 MCP 工具",
                        extra={
                            "event_code": "mcp.call.start",
                            "tool": normalized_tool_name,
                            "attempt": attempt,
                            "base_url": base_url,
                        },
                    )

                    response = await client.post(url, json={"params": params})
                    response.raise_for_status()

                    payload = response.json()
                    if not payload.get("success"):
                        error = payload.get("error") or {}
                        status = "tool_error"
                        raise MCPToolError(
                            tool_name=normalized_tool_name,
                            cause=error.get("message", "Unknown error"),
                        )

                    record_mcp_tool_call(normalized_tool_name, "success")
                    return payload.get("data") or {}

                except httpx.TimeoutException as exc:
                    status = "timeout"
                    last_error = exc
                    continue

                except httpx.ConnectError as exc:
                    status = "connection_error"
                    last_error = exc
                    continue

                except (httpx.RemoteProtocolError, httpx.ReadError, httpx.TransportError) as exc:
                    # 服务端关闭了 Keep-Alive 连接，httpx 复用了死连接导致此错误
                    # 必须销毁整个 client 以清空连接池，再重试
                    status = "connection_error"
                    last_error = exc
                    await self._reset_client()
                    await asyncio.sleep(0.2)  # 稍作等待再重建连接
                    continue

                except httpx.HTTPStatusError as exc:
                    status = "http_error"
                    record_mcp_tool_call(normalized_tool_name, "http_error")
                    raise MCPToolError(normalized_tool_name, f"HTTP {exc.response.status_code}") from exc

                except MCPToolError:
                    record_mcp_tool_call(normalized_tool_name, "tool_error")
                    raise

            if attempt >= self._max_retries:
                if status == "timeout":
                    record_mcp_tool_call(normalized_tool_name, "timeout")
                    raise MCPTimeoutError(normalized_tool_name, self._timeout) from last_error
                if status == "connection_error":
                    record_mcp_tool_call(normalized_tool_name, "connection_error")
                    if isinstance(last_error, (httpx.RemoteProtocolError, httpx.ReadError, httpx.TransportError)):
                        raise MCPConnectionError(
                            last_url,
                            f"transport error after retries: {last_error}",
                        ) from last_error
                    raise MCPConnectionError(last_url, str(last_error or "connection failed")) from last_error

            # 指数退避重试
            delay = self._retry_delay * (2 ** attempt)
            logger.warning(
                "MCP 调用失败，准备重试",
                extra={
                    "event_code": "mcp.call.retry",
                    "tool": normalized_tool_name,
                    "attempt": attempt,
                    "delay": delay,
                },
            )
            await asyncio.sleep(delay)
        
        # 不应到达这里
        record_mcp_tool_call(normalized_tool_name, "unknown_error")
        raise MCPToolError(normalized_tool_name, "Max retries exceeded")

    async def list_tools(self) -> list[dict[str, Any]]:
        """列出 Server 端所有可用工具"""
        for base_url in self._base_urls:
            url = f"{base_url}/mcp/tools"
            try:
                client = await self._get_client()
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
                return data.get("tools") or []
            except (httpx.ConnectError, httpx.TransportError):
                await self._reset_client()
                continue
        return []
# endregion
