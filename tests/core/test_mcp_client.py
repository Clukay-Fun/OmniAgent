from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.mcp.client import MCPClient  # noqa: E402
from src.utils.exceptions import MCPConnectionError  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def _settings(max_retries: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        mcp=SimpleNamespace(
            base_url="http://mcp.local",
            request=SimpleNamespace(
                timeout=0.1,
                max_retries=max_retries,
                retry_delay=0,
            ),
        )
    )


def test_call_tool_retries_transport_error_with_client_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    clients: list[object] = []
    plans = ["transport_error", "success"]

    class _FakeAsyncClient:
        def __init__(self, **_kwargs) -> None:
            self.plan = plans.pop(0)
            self.is_closed = False
            clients.append(self)

        async def post(self, _url: str, json: dict[str, object]) -> _FakeResponse:
            _ = json
            if self.plan == "transport_error":
                raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
            return _FakeResponse({"success": True, "data": {"ok": True}})

        async def aclose(self) -> None:
            self.is_closed = True

    async def _no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("src.mcp.client.httpx.AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr("src.mcp.client.asyncio.sleep", _no_sleep)

    client = MCPClient(_settings(max_retries=1))
    result = asyncio.run(client.call_tool("feishu.v1.bitable.record.query", {"q": "x"}))

    assert result == {"ok": True}
    assert len(clients) == 2
    assert clients[0].is_closed is True


def test_call_tool_raises_connection_error_after_transport_retries_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    clients: list[object] = []
    plans = ["transport_error", "transport_error"]

    class _FakeAsyncClient:
        def __init__(self, **_kwargs) -> None:
            self.plan = plans.pop(0)
            self.is_closed = False
            clients.append(self)

        async def post(self, _url: str, json: dict[str, object]) -> _FakeResponse:
            _ = json
            if self.plan == "transport_error":
                raise httpx.ReadError("Server disconnected without sending a response.")
            return _FakeResponse({"success": True, "data": {"ok": True}})

        async def aclose(self) -> None:
            self.is_closed = True

    async def _no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("src.mcp.client.httpx.AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr("src.mcp.client.asyncio.sleep", _no_sleep)

    client = MCPClient(_settings(max_retries=1))
    with pytest.raises(MCPConnectionError) as exc_info:
        asyncio.run(client.call_tool("feishu.v1.bitable.record.query", {"q": "x"}))

    assert "transport error after retries" in str(exc_info.value)
    assert "Server disconnected without sending a response." in str(exc_info.value)
    assert len(clients) == 2
    assert clients[0].is_closed is True
    assert clients[1].is_closed is True


def test_call_tool_fallbacks_to_localhost_for_host_ws_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_urls: list[str] = []

    class _FakeAsyncClient:
        def __init__(self, **_kwargs) -> None:
            self.is_closed = False

        async def post(self, url: str, json: dict[str, object]) -> _FakeResponse:
            _ = json
            requested_urls.append(url)
            if "mcp-feishu-server" in url:
                raise httpx.ConnectError("Name or service not known")
            return _FakeResponse({"success": True, "data": {"ok": True}})

        async def aclose(self) -> None:
            self.is_closed = True

    monkeypatch.setattr("src.mcp.client.httpx.AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr("src.mcp.client._is_running_in_container", lambda: False)

    settings = _settings(max_retries=0)
    settings.mcp.base_url = "http://mcp-feishu-server:8081"
    client = MCPClient(settings)
    result = asyncio.run(client.call_tool("feishu.v1.bitable.record.query", {"q": "x"}))

    assert result == {"ok": True}
    assert requested_urls[0].startswith("http://mcp-feishu-server:8081/")
    assert any(url.startswith("http://localhost:8081/") for url in requested_urls)
