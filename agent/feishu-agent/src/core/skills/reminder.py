"""
ReminderSkill - 提醒技能

职责：创建和管理待办提醒
Phase 1：仅存取（Postgres），缺时间默认今天 18:00 并告知
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any

from src.core.skills.base import BaseSkill
from src.core.types import SkillContext, SkillResult

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
    DEFAULT_TIME_HINT = '已设置为今天 {time}，如需修改请回复"修改提醒时间为 XX:XX"。'

    LIST_TRIGGERS = ["查看提醒", "提醒列表", "我的提醒", "查看待办", "待办列表", "查看待办事项"]
    DONE_TRIGGERS = ["完成提醒", "标记完成", "完成", "已完成"]
    CANCEL_TRIGGERS = ["取消提醒", "撤销提醒", "取消", "撤销"]
    DELETE_TRIGGERS = ["删除提醒", "删除"]

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
        reminder_cfg = self._config.get("reminder", {})
        if not reminder_cfg:
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
        chat_id = context.extra.get("chat_id") if context.extra else None
        chat_type = context.extra.get("chat_type") if context.extra else None
        if chat_type == "p2p":
            chat_id = None
        
        # 处理列表/更新类请求
        if self._is_list_request(query):
            return await self._list_reminders(user_id)

        action = self._extract_update_action(query)
        if action:
            return await self._update_reminder(user_id, query, action)

        # 解析提醒内容和时间（创建）
        content = self._extract_content(query)
        remind_time = self._extract_time(query)
        
        if not content:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="未识别到提醒内容",
                reply_text='请告诉我您想提醒什么？例如："提醒我明天开会"',
            )
        
        # 处理缺失时间
        time_hint = ""
        if remind_time is None:
            remind_time = self._get_default_time()
            time_hint = self._default_time_hint.format(time=self._default_time)
        
        priority = self._extract_priority(query)
        
        try:
            reminder_id = await self._save_reminder(
                user_id=user_id,
                chat_id=chat_id,
                content=content,
                remind_time=remind_time,
                priority=priority,
            )
            
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
                    "action": "create",
                    "reminder_id": reminder_id,
                    "content": content,
                    "remind_time": time_str,
                    "priority": priority,
                    "chat_id": chat_id,
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

    def _extract_priority(self, query: str) -> str:
        reminder_cfg = self._config.get("reminder", {})
        if not reminder_cfg:
            reminder_cfg = self._config.get("skills", {}).get("reminder", {})
        priority_keywords = reminder_cfg.get("priority_keywords", {})

        for word in priority_keywords.get("high", []):
            if word in query:
                return "high"
        for word in priority_keywords.get("low", []):
            if word in query:
                return "low"
        return "medium"

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
        has_date_keyword = False
        
        if "明天" in query:
            date_offset = 1
            has_date_keyword = True
        elif "后天" in query:
            date_offset = 2
            has_date_keyword = True
        elif "今天" in query:
            date_offset = 0
            has_date_keyword = True
        
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
        
        # 有日期关键词但无具体时间：使用该日期的默认时间（上午9点）
        if has_date_keyword:
            return datetime.combine(target_date, time(9, 0))
        
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

    def _is_list_request(self, query: str) -> bool:
        return any(trigger in query for trigger in self.LIST_TRIGGERS)

    def _extract_update_action(self, query: str) -> str | None:
        if any(trigger in query for trigger in self.DELETE_TRIGGERS):
            return "delete"
        if any(trigger in query for trigger in self.CANCEL_TRIGGERS):
            return "cancelled"
        if any(trigger in query for trigger in self.DONE_TRIGGERS):
            return "done"
        return None

    def _extract_reminder_id(self, query: str) -> int | None:
        import re

        match = re.search(r"(\d+)", query)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    async def _list_reminders(self, user_id: str) -> SkillResult:
        if not self._db:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="数据库未配置",
                reply_text="当前未配置数据库，无法查询提醒列表。",
            )

        try:
            reminders = await self._db.list_reminders(user_id=user_id, status="pending")
        except Exception as e:
            logger.error(f"Failed to list reminders: {e}")
            return SkillResult(
                success=False,
                skill_name=self.name,
                message=str(e),
                reply_text="查询提醒失败，请稍后重试。",
            )

        if not reminders:
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"action": "list", "total": 0},
                message="暂无提醒",
                reply_text="当前没有待办提醒。",
            )

        lines = [f"📌 我的提醒（共 {len(reminders)} 条）", ""]
        for idx, item in enumerate(reminders, start=1):
            due_at = item.get("due_at")
            due_text = due_at.strftime("%Y-%m-%d %H:%M") if due_at else "未设置时间"
            lines.append(f"{idx}. #{item.get('id')} {item.get('content', '')}")
            lines.append(f"   ⏰ {due_text} ｜ 优先级 {item.get('priority', 'medium')}")

        return SkillResult(
            success=True,
            skill_name=self.name,
            data={"action": "list", "total": len(reminders)},
            message="提醒列表",
            reply_text="\n".join(lines),
        )

    async def _update_reminder(self, user_id: str, query: str, action: str) -> SkillResult:
        if not self._db:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="数据库未配置",
                reply_text="当前未配置数据库，无法更新提醒。",
            )

        reminder_id = self._extract_reminder_id(query)
        if reminder_id is None:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="缺少提醒 ID",
                reply_text='请提供提醒编号，例如："完成提醒 12" 或 "删除提醒 12"。',
            )

        try:
            if action == "delete":
                updated = await self._db.delete_reminder(reminder_id, user_id=user_id)
                verb = "删除"
            else:
                updated = await self._db.update_status(reminder_id, user_id=user_id, status=action)
                verb = "更新"
        except Exception as e:
            logger.error(f"Failed to update reminder: {e}")
            return SkillResult(
                success=False,
                skill_name=self.name,
                message=str(e),
                reply_text="更新提醒失败，请稍后重试。",
            )

        if not updated:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="提醒不存在",
                reply_text="未找到对应的提醒编号，请检查后再试。",
            )

        return SkillResult(
            success=True,
            skill_name=self.name,
            data={"action": action, "reminder_id": reminder_id},
            message="提醒已更新",
            reply_text=f"已{verb}提醒 #{reminder_id}。",
        )

    async def _save_reminder(
        self,
        user_id: str,
        chat_id: str | None,
        content: str,
        remind_time: datetime,
        priority: str,
    ) -> int:
        """
        存储提醒到数据库
        
        Args:
            user_id: 用户 ID
            content: 提醒内容
            remind_time: 提醒时间
            priority: 优先级
        
        Returns:
            reminder_id: 提醒 ID
        """
        if self._db:
            return await self._db.create_reminder(
                user_id=user_id,
                chat_id=chat_id,
                content=content,
                due_at=remind_time,
                priority=priority,
                status="pending",
                source="manual",
            )

        # Mock：生成临时 ID
        import random

        reminder_id = random.randint(1000, 9999)
        logger.info(
            "Reminder saved (mock): %s - %s",
            reminder_id,
            {
                "user_id": user_id,
                "content": content,
                "remind_time": remind_time.isoformat(),
                "priority": priority,
                "chat_id": chat_id,
            },
        )
        return reminder_id
# endregion
# ============================================
