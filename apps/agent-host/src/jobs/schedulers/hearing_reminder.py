"""
描述: 开庭日提醒调度器
主要功能:
    - 定时扫描开庭案件
    - 按提前天数发送提醒
    - 去重机制（避免重复发送）
"""

from __future__ import annotations

import logging
from datetime import datetime, date, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.jobs.reminder_dispatcher import ReminderDispatchPayload, ReminderDispatcher
from src.mcp.client import MCPClient
from src.config import Settings

logger = logging.getLogger(__name__)


# ============================================
# region 开庭日提醒调度器
# ============================================
class HearingReminderScheduler:
    """
    开庭日提醒调度器
    
    功能:
        - 每日定时扫描开庭案件
        - 按提前天数（7/3/1/0天）发送提醒
        - 内存去重（避免重复发送）
    """
    
    def __init__(
        self,
        settings: Settings,
        mcp_client: MCPClient,
        reminder_chat_id: str,
        reminder_offsets: list[int] | None = None,
        interval_minutes: int = 60,
        scan_hour: int = 8,
        scan_minute: int = 0,
        dispatcher: ReminderDispatcher | None = None,
    ) -> None:
        """
        初始化调度器
        
        参数:
            settings: 全局配置
            mcp_client: MCP 客户端
            reminder_chat_id: 提醒接收者 chat_id
            reminder_offsets: 提醒提前天数列表（默认 [7, 3, 1, 0]）
            scan_hour: 扫描时间（小时，默认 8）
            scan_minute: 扫描时间（分钟，默认 0）
        """
        self._settings = settings
        self._mcp = mcp_client
        self._reminder_chat_id = reminder_chat_id
        self._reminder_offsets = reminder_offsets or [7, 3, 1, 0]
        self._interval_minutes = max(1, int(interval_minutes))
        self._scan_hour = scan_hour
        self._scan_minute = scan_minute
        self._dispatcher = dispatcher or ReminderDispatcher(settings=settings)
        
        self._scheduler = AsyncIOScheduler()
    
    def start(self) -> None:
        """启动调度器"""
        self._scheduler.add_job(
            self._scan_and_remind,
            "interval",
            minutes=self._interval_minutes,
            misfire_grace_time=max(60, self._interval_minutes * 60),
            coalesce=True,
            max_instances=1,
        )
        self._scheduler.start()
        logger.info(
            f"Hearing reminder scheduler started: "
            f"scan interval={self._interval_minutes}m, "
            f"offsets={self._reminder_offsets}"
        )
    
    async def stop(self) -> None:
        """停止调度器"""
        self._scheduler.shutdown(wait=False)
        logger.info("Hearing reminder scheduler stopped")
    
    async def _scan_and_remind(self) -> None:
        """扫描并发送提醒"""
        try:
            today = date.today()
            
            # 对每个提前天数进行扫描
            for offset in self._reminder_offsets:
                target_date = today + timedelta(days=offset)
                await self._scan_date(target_date, offset)
                
        except Exception as e:
            logger.error(f"Hearing reminder scan error: {e}", exc_info=True)
    
    async def _scan_date(self, target_date: date, offset: int) -> None:
        """
        扫描指定日期的开庭案件
        
        参数:
            target_date: 目标开庭日期
            offset: 提前天数
        """
        try:
            # 调用 MCP 搜索指定日期的开庭案件
            date_str = target_date.strftime("%Y-%m-%d")
            result = await self._mcp.call_tool(
                "feishu.v1.bitable.search_date_range",
                {
                    "field": "开庭日",
                    "date_from": date_str,
                    "date_to": date_str,
                    "limit": 100,
                }
            )
            
            records = result.get("records", [])
            if not records:
                logger.debug(f"No hearings found for {date_str} (offset={offset})")
                return
            
            logger.info(f"Found {len(records)} hearings for {date_str} (offset={offset})")
            
            # 发送提醒
            for record in records:
                await self._send_reminder(record, offset, target_date)
                
        except Exception as e:
            logger.error(f"Error scanning date {target_date}: {e}", exc_info=True)
    
    async def _send_reminder(
        self,
        record: dict[str, Any],
        offset: int,
        hearing_date: date,
    ) -> None:
        """
        发送单条提醒
        
        参数:
            record: 案件记录
            offset: 提前天数
            hearing_date: 开庭日期
        """
        record_id = record.get("record_id")
        if not record_id:
            return
        
        # 提取案件信息
        fields = record.get("fields_text", {})
        case_no = fields.get("案号", "未知案号")
        case_type = fields.get("案由", "")
        court = fields.get("审理法院", "")
        lawyer = fields.get("主办律师", "")
        record_url = record.get("record_url", "")
        
        # 生成提醒消息
        message = self._build_reminder_message(
            case_no=case_no,
            case_type=case_type,
            court=court,
            lawyer=lawyer,
            hearing_date=hearing_date,
            offset=offset,
            record_url=record_url,
        )
        
        # 发送消息
        try:
            result = await self._dispatcher.dispatch(
                ReminderDispatchPayload(
                    source="hearing",
                    business_id=str(record_id),
                    trigger_date=hearing_date,
                    offset=offset,
                    receive_id=self._reminder_chat_id,
                    msg_type="text",
                    content={"text": message},
                    receive_id_type="chat_id",
                    target_conversation_id=self._reminder_chat_id,
                    credential_source="org_b",
                )
            )
            if result.status == "deduped":
                logger.info(f"Hearing reminder deduped: {case_no} (offset={offset})")
                return
            logger.info(f"Hearing reminder sent: {case_no} (offset={offset})")
            
        except Exception as e:
            logger.error(f"Failed to send reminder for {case_no}: {e}", exc_info=True)
    
    def _build_reminder_message(
        self,
        case_no: str,
        case_type: str,
        court: str,
        lawyer: str,
        hearing_date: date,
        offset: int,
        record_url: str,
    ) -> str:
        """
        构建提醒消息
        
        参数:
            case_no: 案号
            case_type: 案由
            court: 法院
            lawyer: 律师
            hearing_date: 开庭日期
            offset: 提前天数
            record_url: 记录链接
            
        返回:
            消息文本
        """
        # 根据提前天数确定紧急程度
        if offset == 0:
            urgency = "🔴 今天开庭"
            emoji = "🚨"
        elif offset == 1:
            urgency = "🟠 明天开庭"
            emoji = "⚠️"
        elif offset == 3:
            urgency = "🟡 3天后开庭"
            emoji = "📅"
        else:
            urgency = f"🟢 {offset}天后开庭"
            emoji = "📌"
        
        date_str = hearing_date.strftime("%Y年%m月%d日")
        
        message_parts = [
            f"{emoji} {urgency}",
            "",
            f"📋 案号：{case_no}",
        ]
        
        if case_type:
            message_parts.append(f"📝 案由：{case_type}")
        if court:
            message_parts.append(f"🏛 法院：{court}")
        if lawyer:
            message_parts.append(f"👤 律师：{lawyer}")
        
        message_parts.extend([
            f"📆 开庭日期：{date_str}",
            "",
            f"🔗 查看详情：{record_url}",
        ])
        
        return "\n".join(message_parts)
# endregion
