"""
Agent orchestrator - 核心编排层

职责：整合 IntentParser + SkillRouter，统一处理用户消息
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.core.session import SessionManager
from src.core.intent import IntentParser, load_skills_config
from src.core.router import SkillRouter, SkillContext, ContextManager
from src.core.skills import QuerySkill, SummarySkill, ReminderSkill, ChitchatSkill
from src.core.soul import SoulManager
from src.core.memory import MemoryManager
from src.db.postgres import PostgresClient
from src.config import Settings
from src.llm.client import LLMClient
from src.mcp.client import MCPClient
from src.utils.time_parser import parse_time_range

logger = logging.getLogger(__name__)


# ============================================
# region AgentOrchestrator
# ============================================
class AgentOrchestrator:
    """
    Agent 编排器
    
    职责：
    1. 接收用户消息
    2. 调用 IntentParser 解析意图
    3. 通过 SkillRouter 路由到对应技能
    4. 返回格式化结果
    """
    
    _DATE_PATTERN = re.compile(
        r"(?:\d{4}年)?\d{1,2}月\d{1,2}[日号]?|\d{4}-\d{1,2}-\d{1,2}"
    )

    def __init__(
        self,
        settings: Settings,
        session_manager: SessionManager,
        mcp_client: MCPClient,
        llm_client: LLMClient,
        skills_config_path: str = "config/skills.yaml",
    ) -> None:
        """
        Args:
            settings: 应用配置
            session_manager: 会话管理器
            mcp_client: MCP 客户端
            llm_client: LLM 客户端
            skills_config_path: 技能配置文件路径
        """
        self._settings = settings
        self._sessions = session_manager
        self._mcp = mcp_client
        self._llm = llm_client
        
        # 加载技能配置
        self._skills_config = load_skills_config(skills_config_path)

        # 初始化 Soul 与 Memory
        self._soul_manager = SoulManager()
        self._memory_manager = MemoryManager()
        self._memory_manager.cleanup_logs()

        # 初始化 Postgres
        self._db = PostgresClient(settings.postgres) if settings.postgres.dsn else None
        
        # 初始化意图解析器
        self._intent_parser = IntentParser(
            skills_config=self._skills_config,
            llm_client=llm_client,
        )
        
        # 初始化技能路由器
        max_hops = self._skills_config.get("chain", {}).get(
            "max_hops",
            self._skills_config.get("routing", {}).get("max_hops", 2),
        )
        self._router = SkillRouter(
            skills_config=self._skills_config,
            max_hops=max_hops,
        )
        
        # 初始化上下文管理器
        self._context_manager = ContextManager()
        
        # 注册技能
        self._register_skills()

    def _register_skills(self) -> None:
        """注册所有技能"""
        skills = [
            QuerySkill(mcp_client=self._mcp, settings=self._settings),
            SummarySkill(llm_client=self._llm, skills_config=self._skills_config),
            ReminderSkill(db_client=self._db, skills_config=self._skills_config),
            ChitchatSkill(skills_config=self._skills_config),
        ]
        self._router.register_all(skills)
        logger.info(f"Registered skills: {self._router.list_skills()}")

    async def handle_message(self, user_id: str, text: str) -> dict[str, Any]:
        """
        处理用户消息
        
        Args:
            user_id: 用户 ID
            text: 用户输入文本
            
        Returns:
            回复数据（type, text, card 等）
        """
        import time
        from src.utils.logger import set_request_context, clear_request_context, generate_request_id
        from src.utils.metrics import record_request, record_intent_parse, set_active_sessions
        
        # 设置请求上下文（用于结构化日志）
        request_id = generate_request_id()
        set_request_context(request_id=request_id, user_id=user_id)
        
        start_time = time.perf_counter()
        status = "success"
        
        try:
            # 清理过期会话和上下文
            self._sessions.cleanup_expired()
            self._context_manager.cleanup_expired()
            
            # 更新活跃会话指标
            set_active_sessions(self._context_manager.active_count())
            
            # 记录用户消息
            self._sessions.add_message(user_id, "user", text)
            
            # Step 1: 构建 LLM 上下文并解析意图
            llm_context = self._build_llm_context(user_id)
            intent_start = time.perf_counter()
            intent = await self._intent_parser.parse(text, llm_context=llm_context)
            record_intent_parse(intent.method, time.perf_counter() - intent_start)
            
            logger.info(
                "Intent parsed",
                extra={
                    "query": text,
                    "intent": intent.to_dict(),
                },
            )
            
            # Step 2: 构建执行上下文
            # 尝试获取上次查询结果（用于链式调用）
            prev_context = self._context_manager.get(user_id)
            extra = await self._build_extra(text, user_id, llm_context)
            
            context = SkillContext(
                query=text,
                user_id=user_id,
                last_result=prev_context.last_result if prev_context else None,
                last_skill=prev_context.last_skill if prev_context else None,
                extra=extra,
            )
            
            # Step 3: 路由并执行技能
            result = await self._router.route(intent, context)
            
            # Step 4: 更新上下文（保存结果供后续链式调用）
            if result.success and result.data:
                self._context_manager.update_result(user_id, result.skill_name, result.data)
                # 更新完整上下文
                self._context_manager.set(user_id, context.with_result(result.skill_name, result.data))
            
            if not result.success:
                status = "failure"
            
            # Step 5: 构建回复
            reply = result.to_reply()

            # Step 6: 写入记忆（日志 + 关键偏好）
            self._record_memory(
                user_id,
                text,
                reply.get("text", ""),
                result.skill_name,
                result.data or {},
                context.extra,
            )
            
        except Exception as e:
            status = "error"
            logger.error(f"Message handling error: {e}", exc_info=True)
            reply = {
                "type": "text",
                "text": self._settings.reply.templates.error.format(message=str(e)),
            }
        finally:
            # 记录请求指标
            duration = time.perf_counter() - start_time
            record_request("handle_message", status)
            
            logger.info(
                "Request completed",
                extra={
                    "status": status,
                    "duration_ms": round(duration * 1000, 2),
                },
            )
            
            # 清除请求上下文
            clear_request_context()
        
        # 记录助手回复
        self._sessions.add_message(user_id, "assistant", reply.get("text", ""))
        
        return reply

    async def _build_extra(
        self,
        text: str,
        user_id: str,
        llm_context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        构建额外上下文数据（时间范围等）
        
        Args:
            text: 用户输入
            
        Returns:
            extra 字典
        """
        extra: dict[str, Any] = {}
        
        # Soul + Memory
        if llm_context is None:
            llm_context = self._build_llm_context(user_id)

        context_data = llm_context or {}
        extra["soul_prompt"] = context_data.get("soul_prompt", "")
        extra["shared_memory"] = context_data.get("shared_memory", "")
        extra["user_memory"] = context_data.get("user_memory", "")
        extra["recent_logs"] = context_data.get("recent_logs", "")

        # 解析时间范围
        date_range = await self._resolve_time_range(text, llm_context)
        if date_range:
            extra["date_from"] = date_range.get("date_from")
            extra["date_to"] = date_range.get("date_to")
        
        return extra

    def _record_memory(
        self,
        user_id: str,
        user_text: str,
        reply_text: str,
        skill_name: str,
        result_data: dict[str, Any],
        extra: dict[str, Any],
    ) -> None:
        """写入对话日志与用户记忆"""
        try:
            self._memory_manager.append_daily_log(user_id, f"用户: {user_text}")
            self._memory_manager.append_daily_log(user_id, f"助手({skill_name}): {reply_text}")
        except Exception:
            return

        try:
            remembered = self._extract_memory_trigger(user_text)
            if remembered:
                self._memory_manager.remember_user(user_id, remembered)
        except Exception:
            return

        self._record_event(user_id, skill_name, result_data, extra)

    def _extract_memory_trigger(self, text: str) -> str | None:
        """识别并提取用户的长期记忆内容"""
        triggers = ["记住", "请记住", "帮我记住"]
        for trigger in triggers:
            if trigger in text:
                content = text.split(trigger, 1)[-1]
                content = content.strip("，。！？：； ")
                return content or None
        return None

    def _record_event(
        self,
        user_id: str,
        skill_name: str,
        result_data: dict[str, Any],
        extra: dict[str, Any],
    ) -> None:
        """自动事件写入策略"""
        if skill_name == "QuerySkill":
            records = result_data.get("records", []) if result_data else []
            total = result_data.get("total") if result_data else None
            count = total if isinstance(total, int) else len(records)
            if count <= 0:
                return

            date_from = extra.get("date_from")
            date_to = extra.get("date_to")
            date_hint = ""
            if date_from or date_to:
                date_hint = f"（时间范围：{date_from or '-'} ~ {date_to or '-'}）"
            event_text = f"事件: 查询案件共 {count} 条{date_hint}"
            self._memory_manager.append_daily_log(user_id, event_text)

        if skill_name == "ReminderSkill":
            content = result_data.get("content") if result_data else None
            remind_time = result_data.get("remind_time") if result_data else None
            if not content and not remind_time:
                return
            event_text = f"事件: 创建提醒 {content or ''} {remind_time or ''}".strip()
            self._memory_manager.append_daily_log(user_id, event_text)

        if skill_name == "SummarySkill":
            total = result_data.get("total") if result_data else None
            count = total if isinstance(total, int) else 0
            if count <= 0:
                return
            event_text = f"事件: 已生成汇总（共 {count} 条）"
            self._memory_manager.append_daily_log(user_id, event_text)

        if skill_name == "ChitchatSkill":
            response_type = result_data.get("type") if result_data else None
            if not response_type:
                return
            event_text = f"事件: 闲聊响应（{response_type}）"
            self._memory_manager.append_daily_log(user_id, event_text)

    def _build_llm_context(self, user_id: str) -> dict[str, str]:
        context: dict[str, str] = {
            "soul_prompt": "",
            "shared_memory": "",
            "user_memory": "",
            "recent_logs": "",
        }
        try:
            context["soul_prompt"] = self._soul_manager.build_system_prompt()
        except Exception:
            context["soul_prompt"] = ""

        try:
            snapshot = self._memory_manager.snapshot(user_id)
            context["shared_memory"] = snapshot.shared_memory
            context["user_memory"] = snapshot.user_memory
            context["recent_logs"] = snapshot.recent_logs
        except Exception:
            context["shared_memory"] = ""
            context["user_memory"] = ""
            context["recent_logs"] = ""

        return context

    def _format_llm_context(self, llm_context: dict[str, str] | None) -> str:
        if not llm_context:
            return ""

        parts = []
        soul_prompt = llm_context.get("soul_prompt", "").strip()
        if soul_prompt:
            parts.append(soul_prompt)

        memory_parts = []
        user_memory = llm_context.get("user_memory", "").strip()
        shared_memory = llm_context.get("shared_memory", "").strip()
        recent_logs = llm_context.get("recent_logs", "").strip()
        if user_memory:
            memory_parts.append(f"用户记忆：\n{user_memory}")
        if shared_memory:
            memory_parts.append(f"团队共享记忆：\n{shared_memory}")
        if recent_logs:
            memory_parts.append(f"最近日志：\n{recent_logs}")

        if memory_parts:
            parts.append("参考记忆：\n" + "\n\n".join(memory_parts))

        return "\n\n".join(parts)

    async def _resolve_time_range(
        self,
        text: str,
        llm_context: dict[str, str] | None = None,
    ) -> dict[str, str] | None:
        """解析时间范围"""
        # 优先使用规则解析
        parsed = parse_time_range(text)
        if parsed:
            return {"date_from": parsed.date_from, "date_to": parsed.date_to}
        
        # 检查是否有时间相关词
        if not self._has_time_hint(text):
            return None
        
        # 尝试 LLM 解析
        try:
            system_context = self._format_llm_context(llm_context)
            content = await self._llm.parse_time_range(text, system_context=system_context)
            if "date_from" in content and "date_to" in content:
                return {"date_from": content["date_from"], "date_to": content["date_to"]}
        except Exception:
            return None
        
        return None

    def _has_time_hint(self, text: str) -> bool:
        """检查是否包含时间相关词"""
        keywords = ["今天", "明天", "本周", "这周", "下周", "本月", "这个月"]
        if any(keyword in text for keyword in keywords):
            return True
        return bool(re.search(r"\d{1,2}月\d{1,2}[日号]?|\d{4}-\d{1,2}-\d{1,2}", text))

    def reload_config(self, config_path: str = "config/skills.yaml") -> None:
        """
        热更新配置
        
        Args:
            config_path: 配置文件路径
        """
        logger.info(f"Reloading skills config from {config_path}")
        self._skills_config = load_skills_config(config_path)
        
        # 重新初始化解析器和路由器
        self._intent_parser = IntentParser(
            skills_config=self._skills_config,
            llm_client=self._llm,
        )
        
        max_hops = self._skills_config.get("chain", {}).get(
            "max_hops",
            self._skills_config.get("routing", {}).get("max_hops", 2),
        )
        self._router = SkillRouter(
            skills_config=self._skills_config,
            max_hops=max_hops,
        )
        
        # 重新注册技能
        self._register_skills()
        logger.info("Skills config reloaded successfully")
# endregion
# ============================================


# ============================================
# region 向后兼容：AgentCore 别名
# ============================================
# 保持向后兼容，允许现有代码继续使用 AgentCore
AgentCore = AgentOrchestrator
# endregion
# ============================================
