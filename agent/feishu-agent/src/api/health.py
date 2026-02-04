"""
描述: 健康检查与指标端点
主要功能:
    - 根路径与 Favicon 占位
    - 服务健康状态 (Health Check)
    - Prometheus 指标暴露 (Metrics)
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from src.utils.metrics import get_metrics, get_metrics_content_type


router = APIRouter()


# region 基础端点
@router.get("/")
async def root() -> dict[str, str]:
    """服务根路径 (版本信息)"""
    return {"status": "ok", "service": "feishu-agent", "version": "0.2.0"}


@router.get("/favicon.ico")
async def favicon() -> Response:
    """Favicon 占位 (避免浏览器 404)"""
    return Response(status_code=204)


@router.get("/health")
async def health() -> dict[str, str]:
    """Kubernetes/Liveness 健康检查探针"""
    return {"status": "ok"}


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus 监控指标抓取端点"""
    return Response(
        content=get_metrics(),
        media_type=get_metrics_content_type(),
    )
# endregion

