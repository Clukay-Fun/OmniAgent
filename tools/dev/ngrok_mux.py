"""
Single-domain path router for ngrok.

Routes:
- /feishu/webhook -> feishu-agent (default: http://127.0.0.1:8088)
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Iterable

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
import uvicorn


WEBHOOK_TARGET = os.getenv("MUX_WEBHOOK_TARGET", "http://127.0.0.1:8088")
UPSTREAM_TIMEOUT_SECONDS = float(os.getenv("MUX_UPSTREAM_TIMEOUT_SECONDS", "2.5"))

logger = logging.getLogger("ngrok_mux")

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}


def _filter_request_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    filtered: dict[str, str] = {}
    for key, value in headers:
        if key.lower() in HOP_BY_HOP_HEADERS:
            continue
        filtered[key] = value
    return filtered


def _filter_response_headers(headers: Iterable[tuple[bytes, bytes]]) -> dict[str, str]:
    filtered: dict[str, str] = {}
    for key_b, value_b in headers:
        key = key_b.decode("latin-1")
        if key.lower() in HOP_BY_HOP_HEADERS:
            continue
        filtered[key] = value_b.decode("latin-1")
    return filtered


def _pick_target(path: str) -> str | None:
    if path.startswith("/feishu/webhook"):
        return WEBHOOK_TARGET
    return None


app = FastAPI(title="ngrok-path-mux")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy(path: str, request: Request) -> Response:
    raw_path = "/" + path
    target = _pick_target(raw_path)
    if not target:
        return JSONResponse(
            {
                "status": "not_routed",
                "path": raw_path,
                "routes": {
                    "/feishu/webhook": WEBHOOK_TARGET,
                },
            },
            status_code=404,
        )

    query = request.url.query
    target_url = f"{target}{raw_path}"
    if query:
        target_url = f"{target_url}?{query}"

    body = await request.body()
    headers = _filter_request_headers(request.headers.items())

    timeout = httpx.Timeout(
        connect=min(1.0, UPSTREAM_TIMEOUT_SECONDS),
        read=UPSTREAM_TIMEOUT_SECONDS,
        write=UPSTREAM_TIMEOUT_SECONDS,
        pool=min(1.0, UPSTREAM_TIMEOUT_SECONDS),
    )

    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            upstream = await client.request(
                request.method,
                target_url,
                content=body,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        logger.warning("Upstream request failed: %s %s (%s)", request.method, target_url, exc)
        return JSONResponse(
            {
                "status": "upstream_unavailable",
                "target": target,
                "path": raw_path,
                "error": str(exc),
            },
            status_code=502,
        )

    response_headers = _filter_response_headers(upstream.headers.raw)
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)


def main() -> None:
    parser = argparse.ArgumentParser(description="ngrok single-domain path mux")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
