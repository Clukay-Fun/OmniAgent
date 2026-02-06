"""
描述: 健康检查端点
主要功能:
    - 根路径与 Favicon 占位
    - 服务健康状态 (Health Check)
"""

from __future__ import annotations

from fastapi import APIRouter, Response


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


# endregion
