"""
Health check and metrics endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from src.utils.metrics import get_metrics, get_metrics_content_type


router = APIRouter()


@router.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok", "service": "feishu-agent", "version": "0.2.0"}


@router.get("/favicon.ico")
async def favicon() -> Response:
    return Response(status_code=204)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus 指标端点"""
    return Response(
        content=get_metrics(),
        media_type=get_metrics_content_type(),
    )

