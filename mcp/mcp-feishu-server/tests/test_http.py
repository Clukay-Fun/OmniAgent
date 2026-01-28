from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI

from src.server.http import router
import src.tools  # noqa: F401


def test_http_routes() -> None:
    async def run() -> None:
        app = FastAPI()
        app.include_router(router)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            health = await client.get("/health")
            assert health.status_code == 200
            tools = await client.get("/mcp/tools")
            assert tools.status_code == 200
            data = tools.json()
            assert any(tool["name"] == "feishu.v1.bitable.search" for tool in data["tools"])

    asyncio.run(run())
