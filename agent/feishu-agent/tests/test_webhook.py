from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI

from src.api.webhook import router


def test_webhook_url_verification() -> None:
    async def run() -> None:
        app = FastAPI()
        app.include_router(router)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/feishu/webhook",
                json={"type": "url_verification", "challenge": "hello"},
            )
            assert response.status_code == 200
            assert response.json()["challenge"] == "hello"

    asyncio.run(run())
