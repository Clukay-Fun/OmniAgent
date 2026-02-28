"""Compatibility shim for src.mcp.client.

Exports full module attributes to keep monkeypatch/test paths stable.
"""

from __future__ import annotations

from src.infra.mcp import client as _impl

_SKIP = {
    "__name__",
    "__loader__",
    "__package__",
    "__spec__",
    "__file__",
    "__cached__",
    "__builtins__",
}

for _name, _value in vars(_impl).items():
    if _name in _SKIP:
        continue
    globals()[_name] = _value

__all__ = [
    "MCPClient",
    "MCPClientError",
    "_is_running_in_container",
    "httpx",
    "asyncio",
]
