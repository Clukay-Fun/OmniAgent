"""
ReminderSkill - 提醒技能

职责：创建和管理待办提醒
Phase 1：仅存取（Postgres），缺时间默认今天 18:00 并告知
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any

from src.agent.router import BaseSkill, SkillContext, SkillResult

logger = logging.getLogger(__name__)


# ============================================
# region ReminderSkill
# ============================================
class ReminderSkill(BaseSkill):
    """
    提醒技能
    
    功能：
    - 解析用户提醒请求
    - 提取时间和内容
    - 缺时间时默认今天 18:00，并告知用户
    - Phase 1：存储到 Postgres
    - Phase 2：定时推送（待实现）
    """
    
    name: str = "ReminderSkill"
    description: str = "创建提醒、待办事项"

    # 默认提醒时间
    DEFAULT_TIME = "18:00"
    
    # 默认时间提示语
    DEFAULT_TIME_HINT = "已设置为今天 {time}，如需修改请回复"修改提醒时间为 XX:XX"。"

    def __init__(
        self,
        db_client: Any = None,
        skills_config: dict[str, Any] | None = None,
    ) -> None:
        """
        Args:
            db_client: 数据库客户端（用于存储提醒）
            skills_config: skills.yaml 配置
        """
        self._db = db_client
        self._config = skills_config or {}
        
        # 从配置加载默认值
        reminder_cfg = self._config.get("skills", {}).get("reminder", {})
        self._default_time = reminder_cfg.get("default_time", self.DEFAULT_TIME)
        self._default_time_hint = reminder_cfg.get("default_time_hint", self.DEFAULT_TIME_HINT)

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        执行提醒创建
        
        Args:
            context: 执行上下文
            
        Returns:
            SkillResult: 创建结果
        """
        query = context.query
        user_id = context.user_id
        
        # 解析提醒内容和时间
        content = self._extract_content(query)
        remind_time = self._extract_time(query)
        
        if not content:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="未识别到提醒内容",
                reply_text="请告诉我您想提醒什么？例如："提醒我明天开会"",
            )
        
        # 处理缺失时间
        time_hint = ""
        if remind_time is None:
            remind_time = self._get_default_time()
            time_hint = self._default_time_hint.format(time=self._default_time)
        
        # 存储提醒（Phase 1）
        reminder_data = {
            "user_id": user_id,
            "content": content,
            "remind_time": remind_time.isoformat(),
            "created_at": datetime.now().isoformat(),
            "status": "pending",
        }
        
        try:
            reminder_id = await self._save_reminder(reminder_data)
            
            # 构建回复
            time_str = remind_time.strftime("%Y-%m-%d %H:%M")
            reply_lines = [
                "✅ 提醒已创建",
                "",
                f"📌 内容：{content}",
                f"⏰ 时间：{time_str}",
            ]
            if time_hint:
                reply_lines.append("")
                reply_lines.append(f"💡 {time_hint}")
            
            reply_text = "\n".join(reply_lines)
            
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={
                    "reminder_id": reminder_id,
                    "content": content,
                    "remind_time": time_str,
                },
                message="提醒创建成功",
                reply_text=reply_text,
            )
            
        except Exception as e:
            logger.error(f"Failed to save reminder: {e}")
            return SkillResult(
                success=False,
                skill_name=self.name,
                message=str(e),
                reply_text="提醒创建失败，请稍后重试。",
            )

    def _extract_content(self, query: str) -> str | None:
        """提取提醒内容"""
        # 移除常见的提醒关键词
        content = query
        prefixes = [
            "提醒我", "帮我提醒", "提醒一下", "记得", "别忘了",
            "到时候", "待办", "备忘",
        ]
        for prefix in prefixes:
            if content.startswith(prefix):
                content = content[len(prefix):]
                break
            if prefix in content:
                content = content.replace(prefix, "")
        
        # 移除时间表达式（简化处理）
        time_patterns = [
            "今天", "明天", "后天", "下周", "本周",
            "上午", "下午", "晚上", "早上",
        ]
        for pattern in time_patterns:
            content = content.replace(pattern, "")
        
        # 清理
        content = content.strip("，。！？ ")
        return content if content else None

    def _extract_time(self, query: str) -> datetime | None:
        """
        提取提醒时间
        
        支持的格式：
        - 今天、明天、后天
        - 下午3点、晚上8点
        - 具体时间如 14:30
        """
        import re
        
        now = datetime.now()
        
        # 日期偏移
        date_offset = 0
        if "今天" in query:
            date_offset = 0
        elif "明天" in query:
            date_offset = 1
        elif "后天" in query:
            date_offset = 2
        
        target_date = now.date() + timedelta(days=date_offset)
        
        # 提取时间
        # 匹配 "下午3点" / "晚上8点" / "上午10点"
        period_match = re.search(r"(上午|下午|晚上|早上)(\d{1,2})点", query)
        if period_match:
            period = period_match.group(1)
            hour = int(period_match.group(2))
            if period in ("下午", "晚上") and hour < 12:
                hour += 12
            return datetime.combine(target_date, time(hour, 0))
        
        # 匹配 "14:30" / "14点30"
        time_match = re.search(r"(\d{1,2})[点:](\d{2})?", query)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return datetime.combine(target_date, time(hour, minute))
        
        # 仅有日期偏移，无具体时间
        if date_offset > 0:
            # 有日期但无时间，仍返回 None 触发默认时间
            return None
        
        return None

    def _get_default_time(self) -> datetime:
        """获取默认提醒时间（今天 18:00）"""
        now = datetime.now()
        hour, minute = map(int, self._default_time.split(":"))
        default = datetime.combine(now.date(), time(hour, minute))
        
        # 如果已过默认时间，设为明天
        if default <= now:
            default += timedelta(days=1)
        
        return default

    async def _save_reminder(self, data: dict[str, Any]) -> str:
        """
        存储提醒到数据库
        
        Args:
            data: 提醒数据
            
        Returns:
            reminder_id: 提醒 ID
        """
        if self._db:
            # 实际存储逻辑（待实现）
            # return await self._db.insert("reminders", data)
            pass
        
        # Mock：生成临时 ID
        import uuid
        reminder_id = str(uuid.uuid4())[:8]
        logger.info(f"Reminder saved (mock): {reminder_id} - {data}")
        return reminder_id
# endregion
# ============================================
