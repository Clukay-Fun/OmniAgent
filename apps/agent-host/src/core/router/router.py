"""
描述: 技能路由与链式执行管理器
主要功能:
    - 意图(Intent) -> 技能(Skill) 路由分发
    - 支持单一技能执行与链式(Chain)执行
    - 全局上下文生命周期管理 (ContextManager)
    - 运行时异常处理与降级策略
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from src.core.intent import IntentResult, SkillMatch
from src.core.types import SkillContext, SkillExecutionStatus, SkillResult

if TYPE_CHECKING:
    from src.core.skills.base import BaseSkill

logger = logging.getLogger(__name__)


# 统一的技能名映射表（支持别名 -> 标准名 双向映射）
_SKILL_NAME_MAP: dict[str, str] = {
    # 小写别名 -> 标准名
    "query": "QuerySkill",
    "create": "CreateSkill",
    "update": "UpdateSkill",
    "delete": "DeleteSkill",
    "summary": "SummarySkill",
    "reminder": "ReminderSkill",
    "chitchat": "ChitchatSkill",
    # 标准名 -> 标准名（方便统一查找）
    "QuerySkill": "QuerySkill",
    "CreateSkill": "CreateSkill",
    "UpdateSkill": "UpdateSkill",
    "DeleteSkill": "DeleteSkill",
    "SummarySkill": "SummarySkill",
    "ReminderSkill": "ReminderSkill",
    "ChitchatSkill": "ChitchatSkill",
}


# ============================================
# region SkillRouter 核心类
# ============================================
class SkillRouter:
    """
    技能路由器核心类
    
    功能:
        - 维护已注册技能表
        - 解析意图并调度技能执行
        - 处理技能链 (Chain) 逻辑
        - 统一异常捕获与指标记录
    """

    # 使用模块级统一映射表
    SKILL_NAME_MAP = _SKILL_NAME_MAP

    def __init__(
        self,
        skills_config: dict[str, Any],
        max_hops: int = 2,
    ) -> None:
        self._config = skills_config
        self._max_hops = max_hops
        self._skills: dict[str, BaseSkill] = {}
        self._chains = skills_config.get("chains", {})
        self._chain_config = skills_config.get("chain", {})

    def register(self, skill: BaseSkill) -> None:
        self._skills[skill.name] = skill
        logger.debug(
            "已注册技能: %s",
            skill.name,
            extra={"event_code": "router.skill.registered"},
        )

    def register_all(self, skills: list[BaseSkill]) -> None:
        for skill in skills:
            self.register(skill)

    def get_skill(self, name: str) -> BaseSkill | None:
        return self._skills.get(name)

    def list_skills(self) -> list[str]:
        return list(self._skills.keys())

    async def route(
        self,
        intent: IntentResult,
        context: SkillContext,
    ) -> SkillResult:
        """
        核心路由入口

        逻辑:
            1. 检查意图是否为空
            2. 判断是否为链式意图 (Chain)
            3. 执行单个技能或技能链
            
        参数:
            intent: 意图分析结果
            context: 当前会话上下文
            
        返回:
            SkillResult: 执行结果
        """
        top_skill = intent.top_skill()
        if not top_skill:
            return self._fallback_result("无法识别意图")

        if intent.is_chain:
            chain_sequence = self._resolve_chain(context.query)
            if chain_sequence:
                return await self._execute_chain(chain_sequence, context)

        return await self._execute_skill(top_skill.name, context)

    async def _execute_skill(
        self,
        skill_name: str,
        context: SkillContext,
    ) -> SkillResult:
        """
        执行单个技能 (带监控与错误处理)
        
        流程:
            1. 规范化技能名称
            2. 查找技能实例 (未找到则降级到 Chitchat)
            3. 执行并记录耗时/结果
            4. 处理超时与异常
        """
        from src.utils.metrics import record_skill_execution
        from src.utils.exceptions import (
            LLMTimeoutError,
            MCPTimeoutError,
            SkillTimeoutError,
            get_user_message,
        )

        normalized_name = self._normalize_skill_name(skill_name)
        skill = self._skills.get(normalized_name)
        if not skill and normalized_name != "ChitchatSkill":
            fallback_skill = self._skills.get("ChitchatSkill")
            if fallback_skill:
                logger.warning(
                    "技能不存在，降级到闲聊技能: %s",
                    normalized_name,
                    extra={"event_code": "router.skill.fallback_to_chitchat"},
                )
                normalized_name = "ChitchatSkill"
                skill = fallback_skill

        if not skill:
            logger.warning(
                "技能未注册: %s",
                normalized_name,
                extra={"event_code": "router.skill.not_found"},
            )
            record_skill_execution(normalized_name, "not_found", 0)
            return self._fallback_result(f"技能 {normalized_name} 未注册")

        global_default = float(self._config.get("defaults", {}).get("timeout_seconds", 10.0))
        skills_cfg = self._config.get("skills")
        skill_cfg: dict[str, Any] = {}
        if isinstance(skills_cfg, dict):
            resolved_cfg = skills_cfg.get(normalized_name)
            if isinstance(resolved_cfg, dict):
                skill_cfg = resolved_cfg
            else:
                alias_key = normalized_name.replace("Skill", "").lower()
                alias_cfg = skills_cfg.get(alias_key)
                if isinstance(alias_cfg, dict):
                    skill_cfg = alias_cfg
        timeout = float(skill_cfg.get("timeout_seconds", global_default))

        start_time = time.perf_counter()
        status = SkillExecutionStatus.SUCCESS

        try:
            logger.info(
                "开始执行技能",
                extra={
                    "event_code": "router.skill.start",
                    "skill": normalized_name,
                    "query": context.query,
                    "hop": context.hop_count,
                    "timeout_seconds": timeout,
                },
            )
            result = await asyncio.wait_for(skill.execute(context), timeout=timeout)

            if not result.success:
                status = SkillExecutionStatus.FAILED

            logger.info(
                "技能执行完成",
                extra={
                    "event_code": "router.skill.completed",
                    "skill": normalized_name,
                    "success": result.success,
                    "duration_ms": round((time.perf_counter() - start_time) * 1000, 2),
                    "close_semantic": str((result.data or {}).get("close_semantic") or ""),
                    "close_profile": str((result.data or {}).get("close_profile") or ""),
                },
            )
            return result
        except asyncio.TimeoutError:
            status = SkillExecutionStatus.TIMEOUT
            logger.warning(
                "技能执行超时: %s (>%ss)",
                normalized_name,
                timeout,
                extra={"event_code": "router.skill.timeout", "skill": normalized_name},
            )
            error = SkillTimeoutError(skill_name=normalized_name, timeout_seconds=timeout)
            reply_text = get_user_message(error) or "抱歉，操作响应超时，请稍后重试。"
            return SkillResult(
                success=False,
                skill_name=normalized_name,
                message=str(error),
                reply_text=reply_text,
            )
        except (LLMTimeoutError, MCPTimeoutError) as e:
            status = SkillExecutionStatus.TIMEOUT
            logger.warning(
                "技能执行超时: %s - %s",
                normalized_name,
                e,
                extra={"event_code": "router.skill.timeout", "skill": normalized_name},
            )
            return SkillResult(
                success=False,
                skill_name=normalized_name,
                message=str(e),
                reply_text=get_user_message(e),
            )
        except Exception as e:
            status = SkillExecutionStatus.ERROR
            logger.error(
                "技能执行异常: %s - %s",
                normalized_name,
                e,
                extra={"event_code": "router.skill.error", "skill": normalized_name},
                exc_info=True,
            )
            return SkillResult(
                success=False,
                skill_name=normalized_name,
                message=f"技能执行出错：{str(e)}",
                reply_text="抱歉，处理请求时遇到问题，请稍后重试。",
            )
        finally:
            duration = time.perf_counter() - start_time
            record_skill_execution(normalized_name, status.value, duration)

    async def _execute_chain(
        self,
        sequence: list[str],
        context: SkillContext,
    ) -> SkillResult:
        current_context = context
        last_result: SkillResult | None = None

        for skill_key in sequence:
            if current_context.hop_count >= self._max_hops:
                logger.warning(
                    "链式执行达到最大跳数: %s",
                    self._max_hops,
                    extra={"event_code": "router.chain.max_hops"},
                )
                break

            skill_name = self._resolve_skill_name(skill_key)
            result = await self._execute_skill(skill_name, current_context)
            last_result = result

            if not result.success:
                logger.warning(
                    "链式执行中断: %s - %s",
                    skill_name,
                    result.message,
                    extra={"event_code": "router.chain.broken", "skill": skill_name},
                )
                break

            current_context = current_context.with_result(skill_name, result.data)

        return last_result or self._fallback_result("链式执行失败")

    def _resolve_chain(self, query: str) -> list[str] | None:
        triggers = self._chain_config.get("triggers", [])
        for trigger in triggers:
            pattern = trigger.get("pattern")
            if pattern and re.search(pattern, query):
                skills = trigger.get("skills", [])
                return [self._resolve_skill_name(name) for name in skills]

        for chain_key, chain_cfg in self._chains.items():
            trigger_keywords = chain_cfg.get("trigger_keywords", [])
            for trigger in trigger_keywords:
                if trigger in query:
                    return chain_cfg.get("sequence", [])
        return None

    def _resolve_skill_name(self, skill_key: str) -> str:
        skills_cfg = self._config.get("skills", {})
        if skill_key in skills_cfg:
            skill_cfg = skills_cfg.get(skill_key, {})
            return self._normalize_skill_name(skill_cfg.get("name", skill_key))
        return self._normalize_skill_name(skill_key)

    def _normalize_skill_name(self, name: str) -> str:
        if name in self.SKILL_NAME_MAP:
            return self.SKILL_NAME_MAP[name]
        lower_name = name.lower()
        for key, value in self.SKILL_NAME_MAP.items():
            if key.lower() == lower_name:
                return value
        return _SKILL_NAME_MAP.get(name, name)

    def _fallback_result(self, message: str) -> SkillResult:
        return SkillResult(
            success=False,
            skill_name="fallback",
            message=message,
            reply_text='抱歉，我暂时无法处理您的请求。试试问我"本周有什么庭"吧！',
        )


# endregion
# ============================================


# ============================================
# region ContextManager（全局上下文管理）
# ============================================
class ContextManager:
    """
    全局上下文管理器 (Memory Store)
    
    功能:
        - 存储用户会话上下文 (SkillContext)
        - 管理上下文生命周期 (TTL 过期清理)
        - 记录最后一次技能执行结果 (用于多轮对话)
    """

    def __init__(self, ttl_minutes: int = 30) -> None:
        self._contexts: dict[str, SkillContext] = {}
        self._timestamps: dict[str, float] = {}
        self._ttl_seconds = ttl_minutes * 60

    def get(self, user_id: str) -> SkillContext | None:
        ctx = self._contexts.get(user_id)
        if ctx:

            self._timestamps[user_id] = time.time()
        return ctx

    def set(self, user_id: str, context: SkillContext) -> None:
        self._contexts[user_id] = context
        self._timestamps[user_id] = time.time()

    def update_result(
        self,
        user_id: str,
        skill_name: str,
        result: dict[str, Any],
    ) -> None:

        ctx = self._contexts.get(user_id)
        if ctx:
            ctx.last_skill = skill_name
            ctx.last_result = result
            self._timestamps[user_id] = time.time()

    def clear(self, user_id: str) -> None:
        self._contexts.pop(user_id, None)
        self._timestamps.pop(user_id, None)

    def cleanup_expired(self) -> None:
        now = time.time()
        expired_users = [
            user_id
            for user_id, ts in self._timestamps.items()
            if now - ts > self._ttl_seconds
        ]
        for user_id in expired_users:
            self._contexts.pop(user_id, None)
            self._timestamps.pop(user_id, None)

        if expired_users:
            logger.debug(
                "已清理过期上下文数量: %s",
                len(expired_users),
                extra={"event_code": "router.context.cleanup"},
            )

    def active_count(self) -> int:
        return len(self._contexts)


# endregion
# ============================================
