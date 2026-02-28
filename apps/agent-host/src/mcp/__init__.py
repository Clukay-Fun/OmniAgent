"""Backward-compatible exports for src.mcp."""

from __future__ import annotations

import warnings

from src.infra.mcp import MCPClient, MCPClientError

warnings.warn(
    "src.mcp is deprecated; use src.infra.mcp instead. "
    "This shim will be removed in a future iteration.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["MCPClient", "MCPClientError"]
