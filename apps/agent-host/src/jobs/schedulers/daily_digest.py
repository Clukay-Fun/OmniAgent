"""
描述: 该模块负责每日摘要的调度和推送
主要功能:
    - 初始化调度器并设置每日摘要的推送时间
    - 构建和推送每日摘要内容
"""

from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.jobs.dispatchers.reminder_dispatcher import ReminderDispatchPayload, ReminderDispatcher
from src.infra.mcp.client import MCPClient


logger = logging.getLogger(__name__)


class DailyDigestScheduler:
    """
    每日摘要调度器

    功能:
        - 初始化调度器并设置每日摘要的推送时间
        - 构建和推送每日摘要内容
    """

    def __init__(
        self,
        mcp_client: MCPClient,
        reminder_chat_id: str,
        schedule: str = "09:00",
        timezone: str = "Asia/Shanghai",
        dispatcher: ReminderDispatcher | None = None,
    ) -> None:
        """
        初始化每日摘要调度器

        功能:
            - 初始化 MCP 客户端、提醒聊天ID、调度时间、时区和分发器
            - 创建异步IO调度器
        """
        self._mcp = mcp_client
        self._reminder_chat_id = str(reminder_chat_id or "").strip()
        self._schedule = str(schedule or "09:00").strip() or "09:00"
        self._timezone = str(timezone or "Asia/Shanghai").strip() or "Asia/Shanghai"
        self._dispatcher = dispatcher
        self._scheduler = AsyncIOScheduler(timezone=self._timezone)

    def start(self) -> None:
        """
        启动调度器

        功能:
            - 解析调度时间
            - 添加每日摘要推送任务到调度器
            - 启动调度器
        """
        hour, minute = self._parse_schedule(self._schedule)
        self._scheduler.add_job(
            self._push_daily_digest,
            "cron",
            hour=hour,
            minute=minute,
            coalesce=True,
            max_instances=1,
        )
        self._scheduler.start()

    async def stop(self) -> None:
        """
        停止调度器

        功能:
            - 关闭调度器
        """
        self._scheduler.shutdown(wait=False)

    async def _push_daily_digest(self) -> None:
        """
        推送每日摘要

        功能:
            - 检查提醒聊天ID和分发器是否有效
            - 构建每日摘要内容
            - 使用分发器推送摘要内容
        """
        if not self._reminder_chat_id or self._dispatcher is None:
            return
        today = date.today()
        sections: list[str] = []

        due_text = await self._safe_section(self._build_today_due_section, today)
        if due_text:
            sections.append(due_text)

        week_new_text = await self._safe_section(self._build_week_new_section, today)
        if week_new_text:
            sections.append(week_new_text)

        pending_text = await self._safe_section(self._build_pending_section)
        if pending_text:
            sections.append(pending_text)

        if not sections:
            sections.append("今日暂无摘要数据。")

        message = "\n\n".join(["📊 每日案件摘要"] + sections)
        await self._dispatcher.dispatch(
            ReminderDispatchPayload(
                source="daily_digest",
                business_id=today.isoformat(),
                trigger_date=today,
                offset=0,
                receive_id=self._reminder_chat_id,
                receive_id_type="chat_id",
                msg_type="text",
                content={"text": message},
                target_conversation_id=self._reminder_chat_id,
                credential_source="org_b",
            )
        )

    async def _build_today_due_section(self, today: date) -> str:
        """
        构建今日到期部分

        功能:
            - 调用 MCP 客户端获取今日到期的记录数量
            - 返回格式化的字符串
        """
        day = today.isoformat()
        result = await self._mcp.call_tool(
            "feishu.v1.bitable.search_date_range",
            {"field": "开庭日", "date_from": day, "date_to": day, "limit": 20},
        )
        count = len(result.get("records", []) if isinstance(result, dict) else [])
        return f"- 今日到期: {count}"

    async def _build_week_new_section(self, today: date) -> str:
        """
        构建本周新增部分

        功能:
            - 计算本周的开始和结束日期
            - 调用 MCP 客户端获取本周新增的记录数量
            - 返回格式化的字符串
        """
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        result = await self._mcp.call_tool(
            "feishu.v1.bitable.search_date_range",
            {
                "field": "创建时间",
                "date_from": week_start.isoformat(),
                "date_to": week_end.isoformat(),
                "limit": 50,
            },
        )
        count = len(result.get("records", []) if isinstance(result, dict) else [])
        return f"- 本周新增: {count}"

    async def _build_pending_section(self) -> str:
        """
        构建待处理部分

        功能:
            - 调用 MCP 客户端获取待处理的记录数量
            - 返回格式化的字符串
        """
        result = await self._mcp.call_tool(
            "feishu.v1.bitable.search_keyword",
            {"keyword": "待处理", "limit": 50, "ignore_default_view": True},
        )
        count = len(result.get("records", []) if isinstance(result, dict) else [])
        return f"- 待处理: {count}"

    async def _safe_section(self, fn: Any, *args: Any) -> str:
        """
        安全调用构建摘要部分的方法

        功能:
            - 尝试调用传入的方法并返回结果
            - 捕获异常并记录警告信息
        """
        try:
            return await fn(*args)
        except Exception:
            logger.warning("daily digest section skipped", exc_info=True)
            return ""

    def _parse_schedule(self, schedule: str) -> tuple[int, int]:
        """
        解析调度时间

        功能:
            - 解析传入的调度时间字符串
            - 返回小时和分钟的整数元组
        """
        raw = str(schedule or "09:00")
        if ":" not in raw:
            return 9, 0
        hour_raw, minute_raw = raw.split(":", 1)
        try:
            hour = max(0, min(23, int(hour_raw)))
            minute = max(0, min(59, int(minute_raw)))
            return hour, minute
        except Exception:
            return 9, 0
