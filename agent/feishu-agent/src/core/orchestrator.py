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
        
        # 初始化意图解析器
        self._intent_parser = IntentParser(
            skills_config=self._skills_config,
            llm_client=llm_client,
        )
        
        # 初始化技能路由器
        max_hops = self._skills_config.get("routing", {}).get("max_hops", 2)
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
            ReminderSkill(db_client=None, skills_config=self._skills_config),
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
        # 清理过期会话
        self._sessions.cleanup_expired()
        
        # 记录用户消息
        self._sessions.add_message(user_id, "user", text)
        
        try:
            # Step 1: 解析意图
            intent = await self._intent_parser.parse(text)
            logger.info(
                "Intent parsed",
                extra={
                    "user_id": user_id,
                    "query": text,
                    "intent": intent.to_dict(),
                },
            )
            
            # Step 2: 构建执行上下文
            # 尝试获取上次查询结果（用于链式调用）
            prev_context = self._context_manager.get(user_id)
            extra = await self._build_extra(text)
            
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
            
            # Step 5: 构建回复
            reply = result.to_reply()
            
        except Exception as e:
            logger.error(f"Message handling error: {e}", exc_info=True)
            reply = {
                "type": "text",
                "text": self._settings.reply.templates.error.format(message=str(e)),
            }
        
        # 记录助手回复
        self._sessions.add_message(user_id, "assistant", reply.get("text", ""))
        
        return reply

    async def _build_extra(self, text: str) -> dict[str, Any]:
        """
        构建额外上下文数据（时间范围等）
        
        Args:
            text: 用户输入
            
        Returns:
            extra 字典
        """
        extra: dict[str, Any] = {}
        
        # 解析时间范围
        date_range = await self._resolve_time_range(text)
        if date_range:
            extra["date_from"] = date_range.get("date_from")
            extra["date_to"] = date_range.get("date_to")
        
        return extra

    async def _resolve_time_range(self, text: str) -> dict[str, str] | None:
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
            content = await self._llm.parse_time_range(text)
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
        
        max_hops = self._skills_config.get("routing", {}).get("max_hops", 2)
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
