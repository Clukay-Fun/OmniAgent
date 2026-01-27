"""
描述: SSE 事件工具类
主要功能:
    - 标准化 SSE 事件格式
    - 状态枚举定义
依赖: 标准库
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any


# ============================================
# region 状态枚举
# ============================================
class AgentState(str, Enum):
    """
    Agent 执行状态枚举

    状态说明:
        THINKING: 规划/思考中
        EXECUTING: 执行中
        DONE: 完成
        ERROR: 错误
    """

    THINKING = "THINKING"
    EXECUTING = "EXECUTING"
    DONE = "DONE"
    ERROR = "ERROR"


class MatchingState(str, Enum):
    """
    匹配过程状态枚举

    状态说明:
        FILTERING: 筛选候选业绩中
        SCORING: 评分中
        SAVING: 保存结果中
        DONE: 完成
        ERROR: 错误
    """

    FILTERING = "FILTERING"
    SCORING = "SCORING"
    SAVING = "SAVING"
    DONE = "DONE"
    ERROR = "ERROR"
# endregion
# ============================================


# ============================================
# region SSE事件生成
# ============================================
def sse_event(event: str, data: dict[str, Any]) -> str:
    """
    生成标准 SSE 事件字符串

    参数:
        event: 事件类型（status / progress / chunk / done / error）
        data: 事件数据
    返回:
        格式化的 SSE 事件字符串
    """
    json_data = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {json_data}\n\n"


def sse_status(state: str, step: int = 0, total: int = 0, message: str = "") -> str:
    """
    生成状态事件

    参数:
        state: 当前状态
        step: 当前步骤
        total: 总步骤数
        message: 状态描述
    返回:
        SSE 事件字符串
    """
    return sse_event("status", {
        "state": state,
        "step": step,
        "total": total,
        "message": message,
    })


def sse_progress(current: int, total: int, message: str = "") -> str:
    """
    生成进度事件

    参数:
        current: 当前进度
        total: 总数
        message: 进度描述
    返回:
        SSE 事件字符串
    """
    percent = round(current / total * 100, 1) if total > 0 else 0
    return sse_event("progress", {
        "current": current,
        "total": total,
        "percent": percent,
        "message": message,
    })


def sse_chunk(content: str) -> str:
    """
    生成内容分块事件

    参数:
        content: 内容块
    返回:
        SSE 事件字符串
    """
    return sse_event("chunk", {"content": content})


def sse_done(data: dict[str, Any] | None = None) -> str:
    """
    生成完成事件

    参数:
        data: 附加数据
    返回:
        SSE 事件字符串
    """
    return sse_event("done", data or {})


def sse_error(message: str, code: str = "UNKNOWN") -> str:
    """
    生成错误事件

    参数:
        message: 错误信息
        code: 错误代码
    返回:
        SSE 事件字符串
    """
    return sse_event("error", {"message": message, "code": code})
# endregion
# ============================================
