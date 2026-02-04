"""
描述: 飞书 API 响应数据模型
主要功能:
    - 定义通过 Pydantic 验证后的响应结构
    - 提供基础响应封装
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


# region 响应模型
class FeishuResponse(BaseModel):
    """
    飞书 API 通用响应结构
    
    属性:
        code: 状态码 (0 表示成功)
        msg: 错误信息
        data: 响应数据负载
    """
    code: int | None = None
    msg: str | None = None
    data: dict[str, Any] | None = None
# endregion
