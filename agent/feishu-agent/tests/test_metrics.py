import asyncio

import httpx
from fastapi import FastAPI

from src.api.metrics import router


def test_metrics_endpoint() -> None:
    async def run() -> None:
        app = FastAPI()
        app.include_router(router)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/metrics")
            assert response.status_code == 200
            body = response.text
            assert body.startswith("#") or "feishu_agent" in body

    asyncio.run(run())
