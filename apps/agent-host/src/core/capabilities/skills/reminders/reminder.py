"""
描述: 提醒管理技能
主要功能:
    - 待办事项创建 (基于 Postgres)
    - 提醒列表查询与状态管理
    - 自然语言时间提取
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, time, timedelta
import random
import re
from typing import Any

from src.core.skills.base import BaseSkill
from src.core.types import SkillContext, SkillResult

logger = logging.getLogger(__name__)


# region 提醒技能实现
class ReminderSkill(BaseSkill):
    """
    提醒管理技能核心类

    功能:
        - 创建新提醒 (支持自动推断时间)
        - 查询、完成、删除提醒
        - 多轮对话意图提取
    """
    
    name: str = "ReminderSkill"
    description: str = "创建提醒、待办事项"

    # 默认提醒时间
    DEFAULT_TIME = "18:00"
    
    # 默认时间提示语
    DEFAULT_TIME_HINT = '已设置为今天 {time}，如需修改请回复"修改提醒时间为 XX:XX"。'

    LIST_TRIGGERS = [
        "查看提醒",
        "提醒列表",
        "我的提醒",
        "我有哪些提醒",
        "有哪些提醒",
        "查看待办",
        "待办列表",
        "查看待办事项",
    ]
    DONE_TRIGGERS = ["完成提醒", "标记完成", "完成", "已完成"]
    CANCEL_TRIGGERS = ["取消提醒", "撤销提醒", "取消", "撤销"]
    DELETE_TRIGGERS = ["删除提醒", "删除"]

    def __init__(
        self,
        db_client: Any = None,
        mcp_client: Any = None,
        skills_config: dict[str, Any] | None = None,
    ) -> None:
        """
        初始化技能

        参数:
            db_client: 数据库客户端
            skills_config: 技能配置字典
        """
        self._db = db_client
        self._mcp = mcp_client
        self._config = skills_config or {}
        
        # 从配置加载默认值
        reminder_cfg = self._config.get("reminder", {})
        if not reminder_cfg:
            reminder_cfg = self._config.get("skills", {}).get("reminder", {})
        self._default_time = reminder_cfg.get("default_time", self.DEFAULT_TIME)
        self._default_time_hint = reminder_cfg.get("default_time_hint", self.DEFAULT_TIME_HINT)

        calendar_cfg = reminder_cfg.get("calendar") if isinstance(reminder_cfg.get("calendar"), dict) else {}
        self._calendar_enabled = bool(calendar_cfg.get("enabled", False))
        self._calendar_id = str(calendar_cfg.get("calendar_id") or "").strip()
        self._calendar_timezone = str(calendar_cfg.get("timezone") or "Asia/Shanghai").strip()
        self._calendar_duration_minutes = int(calendar_cfg.get("duration_minutes") or 30)
        self._calendar_tool_create = str(calendar_cfg.get("tool_create") or "feishu.v1.calendar.event.create")
        self._calendar_title_prefix = str(calendar_cfg.get("title_prefix") or "提醒：")

    async def execute(self, context: SkillContext) -> SkillResult:
        """
        执行技能逻辑

        参数:
            context: 上下文对象 (包含 query, user_id 等)

        返回:
            SkillResult: 执行结果
        """
        query = context.query
        user_id = context.user_id
        chat_id = context.extra.get("chat_id") if context.extra else None
        chat_type = context.extra.get("chat_type") if context.extra else None
        planner_plan = context.extra.get("planner_plan") if context.extra and isinstance(context.extra.get("planner_plan"), dict) else None
        if chat_type == "p2p":
            chat_id = None

        # Planner 路由优先
        planner_intent = str(planner_plan.get("intent") or "") if isinstance(planner_plan, dict) else ""
        planner_params: dict[str, Any] = {}
        if isinstance(planner_plan, dict):
            raw_params: Any = planner_plan.get("params")
            if isinstance(raw_params, dict):
                planner_params = {str(k): v for k, v in raw_params.items()}

        pending_action_raw = context.extra.get("pending_action") if isinstance(context.extra, dict) else None
        pending_action = pending_action_raw if isinstance(pending_action_raw, dict) else {}
        callback_intent = str(context.extra.get("callback_intent") or "").strip().lower() if isinstance(context.extra, dict) else ""
        if str(pending_action.get("action") or "") == "create_reminder":
            pending_payload_raw = pending_action.get("payload")
            pending_payload = pending_payload_raw if isinstance(pending_payload_raw, dict) else {}
            return await self._execute_pending_auto_reminders(
                user_id=user_id,
                chat_id=chat_id,
                callback_intent=callback_intent,
                payload=pending_payload,
            )

        if planner_intent == "list_reminders":
            return await self._list_reminders(user_id)
        if planner_intent == "cancel_reminder":
            reminder_id = self._extract_planner_reminder_id(planner_params)
            if reminder_id is None:
                return SkillResult(
                    success=False,
                    skill_name=self.name,
                    message="缺少提醒 ID",
                    reply_text='请提供提醒编号，例如："取消提醒 12"。',
                )
            return await self._apply_reminder_action(user_id, reminder_id, "cancelled")

        # 处理列表/更新类请求
        if self._is_list_request(query):
            return await self._list_reminders(user_id)

        action = self._extract_update_action(query)
        if action:
            return await self._update_reminder(user_id, query, action)

        # 解析提醒内容和时间（创建）
        content = self._extract_content(query)
        remind_time = self._extract_time(query)

        if planner_intent == "create_reminder":
            planner_content = str(planner_params.get("content") or "").strip()
            if planner_content:
                content = planner_content
            planner_time = self._parse_planner_time(planner_params.get("remind_time"))
            if planner_time is not None:
                remind_time = planner_time
        
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
            if self._needs_time_clarification(query):
                return SkillResult(
                    success=True,
                    skill_name=self.name,
                    data={"action": "clarify_time"},
                    message="需要澄清提醒时间",
                    reply_text="我需要一个更具体的提醒时间，例如：明天上午9点、下周五下午3点。",
                )

            remind_time = self._get_default_time()
            time_hint = self._default_time_hint.format(time=self._default_time)

        # 拒绝过去时间
        now = datetime.now()
        if remind_time <= now:
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"action": "invalid_time", "remind_time": remind_time.strftime("%Y-%m-%d %H:%M")},
                message="提醒时间已过",
                reply_text="该时间已经过去，请提供一个未来时间（例如：今天18:00、明天上午9点）。",
            )
        
        priority = self._extract_priority(query)
        planner_priority = str(planner_params.get("priority") or "").strip().lower()
        if planner_priority in {"high", "medium", "low"}:
            priority = planner_priority

        calendar_result: dict[str, Any] | None = None
        calendar_error: str | None = None
        if self._calendar_enabled:
            try:
                calendar_result = await self._create_calendar_event(
                    query=query,
                    content=content,
                    remind_time=remind_time,
                    priority=priority,
                    planner_params=planner_params,
                    context=context,
                )
            except Exception as exc:
                calendar_error = str(exc)
                logger.warning("Create team calendar event failed: %s", exc)

        if calendar_result:
            time_str = remind_time.strftime("%Y-%m-%d %H:%M")
            recurrence_text = calendar_result.get("recurrence_text")
            reply_lines = [
                "✅ 提醒已创建到团队日历",
                "",
                f"📌 内容：{content}",
                f"⏰ 时间：{time_str}",
            ]
            if recurrence_text:
                reply_lines.append(f"🔁 重复：{recurrence_text}")
            if calendar_result.get("event_url"):
                reply_lines.append(f"🔗 日历事件：{calendar_result.get('event_url')}")
            if time_hint:
                reply_lines.append("")
                reply_lines.append(f"💡 {time_hint}")

            return SkillResult(
                success=True,
                skill_name=self.name,
                data={
                    "action": "create",
                    "provider": "calendar",
                    "persisted": True,
                    "calendar_id": calendar_result.get("calendar_id"),
                    "event_id": calendar_result.get("event_id"),
                    "event_url": calendar_result.get("event_url"),
                    "content": content,
                    "remind_time": time_str,
                    "priority": priority,
                    "chat_id": chat_id,
                    "rrule": calendar_result.get("rrule", ""),
                },
                message="团队日历提醒创建成功",
                reply_text="\n".join(reply_lines),
            )
        
        try:
            reminder_id, persisted = await self._save_reminder(
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
            if not persisted:
                reply_lines.append("")
                reply_lines.append("⚠️ 当前数据库不可用，已创建临时提醒（服务重启后可能丢失）。")
            if calendar_error:
                reply_lines.append("")
                reply_lines.append("⚠️ 团队日历创建失败，已降级为本地提醒。")
            
            reply_text = "\n".join(reply_lines)
            
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={
                    "action": "create",
                    "reminder_id": reminder_id,
                    "persisted": persisted,
                    "content": content,
                    "remind_time": time_str,
                    "priority": priority,
                    "chat_id": chat_id,
                    "calendar_error": calendar_error,
                },
                message="提醒创建成功" if persisted else "提醒已临时创建",
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

    async def _execute_pending_auto_reminders(
        self,
        *,
        user_id: str,
        chat_id: str | None,
        callback_intent: str,
        payload: dict[str, Any],
    ) -> SkillResult:
        if callback_intent == "cancel":
            return SkillResult(
                success=True,
                skill_name=self.name,
                data={"clear_pending_action": True},
                message="已取消自动提醒",
                reply_text="好的，已取消自动创建提醒。",
            )

        reminders_raw = payload.get("reminders")
        reminders = reminders_raw if isinstance(reminders_raw, list) else []
        if not reminders:
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"clear_pending_action": True},
                message="无可用提醒",
                reply_text="未检测到可创建的提醒。",
            )

        created = 0
        preview: list[str] = []
        for item in reminders[:20]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            remind_time = self._parse_planner_time(item.get("remind_time"))
            if not content or remind_time is None:
                continue
            priority = str(item.get("priority") or "medium").strip().lower() or "medium"
            await self._save_reminder(
                user_id=user_id,
                chat_id=chat_id,
                content=content,
                remind_time=remind_time,
                priority=priority,
            )
            created += 1
            preview.append(f"- {content} @ {remind_time.strftime('%Y-%m-%d %H:%M')}")

        if created <= 0:
            return SkillResult(
                success=False,
                skill_name=self.name,
                data={"clear_pending_action": True},
                message="自动提醒创建失败",
                reply_text="自动提醒创建失败，请稍后重试。",
            )

        return SkillResult(
            success=True,
            skill_name=self.name,
            data={
                "clear_pending_action": True,
                "action": "create_batch",
                "created_count": created,
            },
            message="自动提醒创建成功",
            reply_text=f"✅ 已创建 {created} 条自动提醒\n" + "\n".join(preview),
        )

    def _extract_content(self, query: str) -> str | None:
        """从 Query 中提取提醒内容的核心部分 (去除无关词)"""
        content = str(query or "").strip()

        # 移除开头动作词
        lead_patterns = [
            r"^(请)?(帮我)?(新增|添加|创建|设置)?提醒(一下)?[：:,，\s]*",
            r"^(请)?(帮我)?提醒我[：:,，\s]*",
            r"^(请)?(帮我)?(新增|添加|创建)待办(事项)?[：:,，\s]*",
            r"^(记得|别忘了)[：:,，\s]*",
        ]
        for pattern in lead_patterns:
            new_content = re.sub(pattern, "", content)
            if new_content != content:
                content = new_content
                break

        # 移除常见干扰词
        noise_tokens = ["提醒", "提醒一下", "到时候", "待办", "备忘", "新增", "创建", "设置"]
        for token in noise_tokens:
            if content == token:
                content = ""
                break
        
        # 移除时间表达式（简化处理）
        time_patterns = [
            "今天", "明天", "后天", "下周", "本周",
            "上午", "下午", "晚上", "早上",
        ]
        for pattern in time_patterns:
            content = content.replace(pattern, "")

        # 清理
        content = content.strip("，。！？ ")
        if content in {"新增提醒", "创建提醒", "设置提醒", "提醒"}:
            return None
        return content if content else None

    def _extract_priority(self, query: str) -> str:
        """根据关键词判断优先级 (high/low/medium)"""
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
        从 Query 中提取提醒时间

        支持格式:
            - 相对时间: "10分钟后", "2小时后"
            - 自然语言: "明天下午3点", "后天早上"
            - 绝对时间: "14:30"
        """
        import re
        
        now = datetime.now()

        # 相对时间上限：24小时内
        MAX_RELATIVE_MINUTES = 24 * 60  # 1440 分钟
        MAX_RELATIVE_HOURS = 24

        relative_match = re.search(r"(\d{1,3})\s*分钟后", query)
        if relative_match:
            minutes = int(relative_match.group(1))
            if minutes > MAX_RELATIVE_MINUTES:
                minutes = MAX_RELATIVE_MINUTES
            return now + timedelta(minutes=minutes)

        hour_match = re.search(r"(\d{1,2})\s*小时后", query)
        if hour_match:
            hours = int(hour_match.group(1))
            if hours > MAX_RELATIVE_HOURS:
                hours = MAX_RELATIVE_HOURS
            return now + timedelta(hours=hours)
        
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

        # 周几解析：下周五 / 本周三 / 周一 / 星期天
        week_day_match = re.search(r"(?:(本周|这周|下周))?(?:周|星期)([一二三四五六日天])", query)
        if week_day_match:
            prefix = week_day_match.group(1) or ""
            day_cn = week_day_match.group(2)
            week_map = {
                "一": 0,
                "二": 1,
                "三": 2,
                "四": 3,
                "五": 4,
                "六": 5,
                "日": 6,
                "天": 6,
            }
            target_weekday = week_map[day_cn]
            today_weekday = now.weekday()

            if prefix == "下周":
                next_monday = now.date() + timedelta(days=(7 - today_weekday))
                target_date = next_monday + timedelta(days=target_weekday)
            elif prefix in {"本周", "这周"}:
                this_monday = now.date() - timedelta(days=today_weekday)
                target_date = this_monday + timedelta(days=target_weekday)
            else:
                days_ahead = (target_weekday - today_weekday) % 7
                target_date = now.date() + timedelta(days=days_ahead)

            has_date_keyword = True
        
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
        """获取兜底默认时间 (配置值或 18:00)"""
        now = datetime.now()
        hour, minute = map(int, self._default_time.split(":"))
        default = datetime.combine(now.date(), time(hour, minute))
        
        # 如果已过默认时间，设为明天
        if default <= now:
            default += timedelta(days=1)
        
        return default

    def _needs_time_clarification(self, query: str) -> bool:
        """判断是否需要用户澄清提醒时间。"""
        import re

        normalized = query.replace(" ", "")
        vague_tokens = ["下周", "本周", "这周", "近期", "最近", "以后", "回头", "有空", "抽空", "过几天"]
        has_vague = any(token in normalized for token in vague_tokens)

        # 明确时间信号：明天/后天/今天、周几、具体钟点、绝对日期
        has_explicit = bool(
            re.search(r"(今天|明天|后天)", query)
            or re.search(r"(?:(本周|这周|下周))?(?:周|星期)[一二三四五六日天]", query)
            or re.search(r"\d{1,2}[:点]\d{0,2}", query)
            or re.search(r"\d{4}-\d{1,2}-\d{1,2}", query)
            or re.search(r"\d{1,2}月\d{1,2}日?", query)
        )
        return has_vague and not has_explicit

    def _is_list_request(self, query: str) -> bool:
        """判断是否为列表查询请求"""
        return any(trigger in query for trigger in self.LIST_TRIGGERS)

    def _extract_update_action(self, query: str) -> str | None:
        """提取更新动作 (done/delete/cancelled)"""
        if any(trigger in query for trigger in self.DELETE_TRIGGERS):
            return "delete"
        if any(trigger in query for trigger in self.CANCEL_TRIGGERS):
            return "cancelled"
        if any(trigger in query for trigger in self.DONE_TRIGGERS):
            return "done"
        return None

    def _extract_reminder_id(self, query: str) -> int | None:
        """提取提醒 ID (数字)"""
        import re

        match = re.search(r"(\d+)", query)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    async def _list_reminders(self, user_id: str) -> SkillResult:
        """执行列出提醒逻辑"""
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
        """执行更新提醒状态逻辑"""
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

        return await self._apply_reminder_action(user_id, reminder_id, action)

    async def _apply_reminder_action(self, user_id: str, reminder_id: int, action: str) -> SkillResult:
        """按指定动作更新提醒。"""
        if not self._db:
            return SkillResult(
                success=False,
                skill_name=self.name,
                message="数据库未配置",
                reply_text="当前未配置数据库，无法更新提醒。",
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

    def _extract_planner_reminder_id(self, params: dict[str, Any]) -> int | None:
        rid = params.get("reminder_id")
        if isinstance(rid, int):
            return rid
        if isinstance(rid, str) and rid.strip().isdigit():
            return int(rid.strip())
        return None

    def _parse_planner_time(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str) or not value.strip():
            return None
        text = value.strip().replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    async def _save_reminder(
        self,
        user_id: str,
        chat_id: str | None,
        content: str,
        remind_time: datetime,
        priority: str,
    ) -> tuple[int, bool]:
        """
        持久化提醒记录

        参数:
            user_id: 用户 ID
            content: 提醒内容
            remind_time: 触发时间
            priority: 优先级

        返回:
            tuple[int, bool]: (提醒 ID, 是否已持久化)
        """
        if self._db:
            try:
                reminder_id = await self._db.create_reminder(
                    user_id=user_id,
                    chat_id=chat_id,
                    content=content,
                    due_at=remind_time,
                    priority=priority,
                    status="pending",
                    source="manual",
                )
                return reminder_id, True
            except Exception as exc:
                if not self._is_db_unavailable_error(exc):
                    raise
                logger.warning("Reminder DB unavailable, fallback to mock storage: %s", exc)
                # 降级为临时提醒，避免请求失败
                self._db = None

        # Mock：生成临时 ID
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
        return reminder_id, False

    def _is_db_unavailable_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        keywords = [
            "password authentication failed",
            "authentication failed",
            "connection refused",
            "could not connect",
            "timeout",
            "connection reset",
            "temporary failure",
            "too many connections",
            "server closed the connection",
        ]
        return any(token in message for token in keywords)

    async def _create_calendar_event(
        self,
        query: str,
        content: str,
        remind_time: datetime,
        priority: str,
        planner_params: dict[str, Any],
        context: SkillContext,
    ) -> dict[str, Any] | None:
        if not self._mcp or not self._calendar_enabled:
            return None

        calendar_id = self._resolve_calendar_id(planner_params, context)
        if not calendar_id:
            return None

        recurrence = self._extract_recurrence_rule(query, remind_time)
        end_time = remind_time + timedelta(minutes=max(self._calendar_duration_minutes, 5))

        title = f"{self._calendar_title_prefix}{content}" if self._calendar_title_prefix else content
        description = f"来源：OmniAgent\n优先级：{priority}"

        params: dict[str, Any] = {
            "calendar_id": calendar_id,
            "summary": title,
            "description": description,
            "start_at": remind_time.strftime("%Y-%m-%d %H:%M"),
            "end_at": end_time.strftime("%Y-%m-%d %H:%M"),
            "timezone": self._calendar_timezone,
            "need_notification": True,
        }
        if recurrence.get("rrule"):
            params["rrule"] = recurrence["rrule"]

        result = await self._mcp.call_tool(self._calendar_tool_create, params)
        return {
            "calendar_id": result.get("calendar_id") or calendar_id,
            "event_id": result.get("event_id") or "",
            "event_url": result.get("event_url") or "",
            "rrule": params.get("rrule", ""),
            "recurrence_text": recurrence.get("text", ""),
        }

    def _resolve_calendar_id(self, planner_params: dict[str, Any], context: SkillContext) -> str:
        extra = context.extra or {}
        candidates = [
            planner_params.get("calendar_id"),
            extra.get("calendar_id"),
            self._calendar_id,
            os.getenv("FEISHU_CALENDAR_ID"),
            os.getenv("FEISHU_TEAM_CALENDAR_ID"),
        ]
        for value in candidates:
            calendar_id = str(value or "").strip()
            if calendar_id:
                return calendar_id
        return ""

    def _extract_recurrence_rule(self, query: str, remind_time: datetime) -> dict[str, str]:
        normalized = query.replace(" ", "")

        if any(token in normalized for token in ["每个工作日", "工作日", "周一到周五", "周一至周五"]):
            return {
                "rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
                "text": "工作日",
            }

        if any(token in normalized for token in ["每天", "每日", "日常", "每一天"]):
            return {
                "rrule": "FREQ=DAILY",
                "text": "每天",
            }

        week_match = re.search(r"每周([一二三四五六日天])", normalized)
        if week_match:
            day_map = {
                "一": "MO",
                "二": "TU",
                "三": "WE",
                "四": "TH",
                "五": "FR",
                "六": "SA",
                "日": "SU",
                "天": "SU",
            }
            byday = day_map.get(week_match.group(1), "")
            if byday:
                return {
                    "rrule": f"FREQ=WEEKLY;BYDAY={byday}",
                    "text": f"每周{week_match.group(1)}",
                }

        # 无明确重复词默认单次
        return {
            "rrule": "",
            "text": "",
        }
# endregion
