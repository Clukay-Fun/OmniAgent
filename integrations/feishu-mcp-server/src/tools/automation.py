"""
描述: 自动化调度工具。
主要功能:
    - 创建/查询/取消/恢复 cron 周期任务
"""

from __future__ import annotations

import re
from typing import Any

from croniter import croniter

from src.automation.service import AutomationService
from src.tools.base import BaseTool
from src.tools.registry import ToolRegistry


_WEEKDAY_MAP = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "日": 0,
    "天": 0,
}


def _normalize_hour_minute(hour_text: str, minute_text: str | None) -> tuple[int, int]:
    hour = int(hour_text)
    minute = int(minute_text or 0)
    if hour < 0 or hour > 23:
        raise ValueError("hour must be 0-23")
    if minute < 0 or minute > 59:
        raise ValueError("minute must be 0-59")
    return hour, minute


def _parse_schedule_text_to_cron(schedule_text: str) -> str:
    text = str(schedule_text or "").strip()
    if not text:
        raise ValueError("schedule text is required")

    if croniter.is_valid(text):
        return text

    daily_match = re.search(r"每(?:天|日)(?:[^0-9]{0,8})?(\d{1,2})(?:[:点时](\d{1,2}))?", text)
    if daily_match:
        hour, minute = _normalize_hour_minute(daily_match.group(1), daily_match.group(2))
        return f"{minute} {hour} * * *"

    weekly_match = re.search(r"每周([一二三四五六日天])(?:[^0-9]{0,8})?(\d{1,2})(?:[:点时](\d{1,2}))?", text)
    if weekly_match:
        weekday = _WEEKDAY_MAP.get(weekly_match.group(1), 0)
        hour, minute = _normalize_hour_minute(weekly_match.group(2), weekly_match.group(3))
        return f"{minute} {hour} * * {weekday}"

    hourly_match = re.search(r"每小时(?:[^0-9]{0,4})(\d{1,2})?", text)
    if hourly_match:
        minute_value = int(hourly_match.group(1) or 0)
        if minute_value < 0 or minute_value > 59:
            raise ValueError("minute must be 0-59")
        return f"{minute_value} * * * *"

    raise ValueError("unsupported schedule text, provide cron expression or common Chinese pattern")


def _resolve_cron_expression(params: dict[str, Any]) -> str:
    cron_expr = str(params.get("cron") or "").strip()
    if cron_expr:
        if not croniter.is_valid(cron_expr):
            raise ValueError("invalid cron expression")
        return cron_expr

    schedule_text = str(params.get("schedule_text") or params.get("schedule") or "").strip()
    if not schedule_text:
        raise ValueError("cron or schedule_text is required")

    resolved = _parse_schedule_text_to_cron(schedule_text)
    if not croniter.is_valid(resolved):
        raise ValueError("failed to resolve valid cron expression")
    return resolved


@ToolRegistry.register
class AutomationCronTool(BaseTool):
    name = "feishu.v1.automation.cron.schedule"
    description = "创建/查询/取消/恢复自动化 cron 周期任务"
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["create", "list", "cancel", "resume"],
                "description": "操作类型",
            },
            "cron": {
                "type": "string",
                "description": "标准 cron 表达式（5 段）",
            },
            "schedule_text": {
                "type": "string",
                "description": "自然语言计划，如'每天早上9点发日报'",
            },
            "action": {
                "type": "object",
                "description": "到期后执行的单个动作对象",
            },
            "context": {
                "type": "object",
                "description": "动作上下文",
            },
            "rule_id": {
                "type": "string",
                "description": "可选规则标识",
            },
            "app_token": {
                "type": "string",
                "description": "可选 app_token",
            },
            "table_id": {
                "type": "string",
                "description": "可选 table_id",
            },
            "record_id": {
                "type": "string",
                "description": "可选 record_id",
            },
            "job_id": {
                "type": "string",
                "description": "取消/恢复时的 job_id",
            },
            "status": {
                "type": "string",
                "description": "list 时按状态过滤",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "description": "list 返回上限",
            },
        },
        "required": ["operation"],
    }

    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        operation = str(params.get("operation") or "").strip().lower()
        if operation not in {"create", "list", "cancel", "resume"}:
            raise ValueError("operation must be one of: create, list, cancel, resume")

        service = AutomationService(self.context.settings, self.context.client)

        if operation == "create":
            cron_expr = _resolve_cron_expression(params)
            action = params.get("action")
            if not isinstance(action, dict) or not action:
                raise ValueError("action is required for create")

            context = params.get("context")
            if not isinstance(context, dict):
                context = {}

            result = service.create_cron_job(
                cron_expr=cron_expr,
                action=action,
                context=context,
                app_token=str(params.get("app_token") or ""),
                table_id=str(params.get("table_id") or ""),
                record_id=str(params.get("record_id") or ""),
                rule_id=str(params.get("rule_id") or ""),
            )
            result["operation"] = operation
            return result

        if operation == "list":
            status = str(params.get("status") or "").strip() or None
            limit = int(params.get("limit") or 100)
            items = service.list_cron_jobs(status=status, limit=limit)
            return {
                "operation": operation,
                "count": len(items),
                "items": items,
            }

        job_id = str(params.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("job_id is required for cancel/resume")

        if operation == "cancel":
            result = service.cancel_cron_job(job_id)
            result["operation"] = operation
            return result

        result = service.resume_cron_job(job_id)
        result["operation"] = operation
        return result
