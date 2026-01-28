"""
Health check endpoint.
"""

from __future__ import annotations

from fastapi import APIRouter, Response


router = APIRouter()


@router.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok", "service": "feishu-agent"}


@router.get("/favicon.ico")
async def favicon() -> Response:
    return Response(status_code=204)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
