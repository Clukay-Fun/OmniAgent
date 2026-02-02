"""Prometheus metrics endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Response

from src.utils.metrics import get_metrics, get_metrics_content_type

router = APIRouter()


@router.get("/metrics")
async def metrics() -> Response:
    data = get_metrics()
    return Response(content=data, media_type=get_metrics_content_type())
