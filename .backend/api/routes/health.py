"""
描述: 健康检查路由
主要功能:
    - 服务可用性检查
依赖: fastapi
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])

# ============================================
# region health_check
# ============================================
@router.get("/health")
def health_check() -> dict:
    """
    健康检查

    返回:
        状态信息
    """

    return {"status": "ok"}
# endregion
# ============================================
