"""
描述: 监控指标端点 (独立路由)
主要功能:
    - 暴露 Prometheus 格式监控指标
    - 对接 Prometheus/Grafana
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from src.utils.observability.metrics import get_metrics, get_metrics_content_type

router = APIRouter()


# region 监控指标
@router.get("/metrics")
async def metrics() -> Response:
    """
    获取 Prometheus 格式指标数据
    (CPU, 内存, 技能调用次数等)
    """
    data = get_metrics()
    return Response(content=data, media_type=get_metrics_content_type())
# endregion
